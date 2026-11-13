#!/usr/bin/env python3
"""`convention_score.py` の判定表。

一時フォルダへ小さなプロジェクトを置いて採点し、種別ごとの件数が期待どおりかを確かめる。
期待に書いていない種別は 0 件であることも確かめるので、数えすぎも落ちる。ただし
`UNCHECKED_UNLESS_EXPECTED` の種別は、期待に書いたケースでだけ件数を確かめる。

`VIOLATION_KINDS` / `INDICATOR_KINDS` のうち、表のどのケースでも 1 件以上を期待していない
種別があれば、それも落とす（数えなくなる向きの退行は、表に無い種別では気づけないため）。

使い方:
    python3 convention_score_cases.py

食い違いがあれば終了コード 1。
"""

import sys
import tempfile
from pathlib import Path

from convention_score import INDICATOR_KINDS, VIOLATION_KINDS, score

MONEY = "export type Money = Readonly<{ amount: number }>;\nexport const Money = { create: (amount: number): Money => ({ amount }) };\n"

# doc の無い宣言は、ほかの種別を確かめるための短い置きファイルのほぼすべてに現れる。
UNCHECKED_UNLESS_EXPECTED = {"undocumented-export"}

# （ケースの名前, 置くファイル, 1 件以上を期待する種別と件数）
CASES = [
    (
        "規約どおりのドメインと feature は 0 件",
        {
            "src/domains/money/index.ts": MONEY,
            "src/domains/money/__tests__/money.normal.test.ts": 'import { Money } from "../index";\ntest("金額を作れる", () => { expect(Money.create(1).amount).toBe(1); });\n',
            "src/features/score/index.ts": 'export { ScoreView } from "./components/ScoreView";\n',
            "src/features/score/components/ScoreView.tsx": 'import { Money } from "@/src/domains/money";\nexport const ScoreView = () => <p>{Money.create(1).amount}</p>;\n',
            "app/page.tsx": 'import { ScoreView } from "@/src/features/score";\nexport default function Page() { return <ScoreView />; }\n',
        },
        {},
    ),
    (
        "domains から services・features への import は向きの違反",
        {
            "src/domains/money/index.ts": 'import { total } from "@/src/services/total";\nimport type { Total } from "@/src/services/total";\nimport { ScoreView } from "@/src/features/score";\n' + MONEY,
            "src/services/total/index.ts": "export const total = 1;\n",
            "src/features/score/index.ts": "export const ScoreView = 1;\n",
        },
        {"layer-direction": 2},
    ),
    (
        "utils からドメイン、services から libs、domains から libs への import は向きの違反。libs は domains と utils を読める",
        {
            "src/utils/ArrayEx.ts": "import { Money } from '../domains/money';\nexport const ArrayEx = { Money };\n",
            "src/libs/storage/index.ts": 'import { ArrayEx } from "@/src/utils/ArrayEx";\nimport { Money } from "@/src/domains/money";\nexport const storage = [ArrayEx, Money];\n',
            "src/services/total/index.ts": 'import { storage } from "@/src/libs/storage";\nexport const total = storage;\n',
            "src/domains/money/index.ts": MONEY,
            "src/domains/tile/index.ts": 'import { storage } from "@/src/libs/storage";\n' + MONEY,
        },
        {"layer-direction": 3},
    ),
    (
        "テストが libs のフェイクを読むのは向きの違反にしない。実装が読めば違反",
        {
            "src/libs/random/index.ts": "export const fixedRandom = () => 0;\n",
            "src/services/game/index.ts": 'import { fixedRandom } from "@/src/libs/random";\nexport const game = fixedRandom;\n',
            "src/services/game/__tests__/game.normal.test.ts": 'import { fixedRandom } from "@/src/libs/random";\ntest("a", () => { expect(fixedRandom()).toBe(0); });\n',
            "src/services/game/__tests__/fixture.ts": 'import { fixedRandom } from "@/src/libs/random";\nexport const seed = fixedRandom;\n',
        },
        {"layer-direction": 1},
    ),
    (
        "cli は libs・services・domains を読める。feature を読むのと、cli が読まれるのは向きの違反",
        {
            "src/cli/calc.ts": 'import { read } from "../libs/stdin";\nimport { total } from "../services/total";\nimport { Money } from "../domains/money";\nimport { view } from "../features/score";\nexport const main = [read, total, Money, view];\n',
            "src/libs/stdin/index.ts": "export const read = 1;\n",
            "src/services/total/index.ts": 'import { main } from "../../cli/calc";\nexport const total = 1;\nexport const again = main;\n',
            "src/domains/money/index.ts": MONEY,
            "src/features/score/index.ts": "export const view = 1;\n",
        },
        {"layer-direction": 2, "import-cycle": 1},
    ),
    (
        "コメントの中の import は数えない",
        {
            "src/domains/money/index.ts": '// import { total } from "@/src/services/total";\n' + MONEY,
            "src/services/total/index.ts": "export const total = 1;\n",
        },
        {},
    ),
    (
        "domains・services の React / Next.js への依存",
        {
            "src/domains/money/index.ts": 'import { useState } from "react";\n' + MONEY,
            "src/services/total/index.ts": 'import { cookies } from "next/headers";\nexport const total = cookies;\n',
            "src/features/score/index.ts": 'import { useState } from "react";\nexport const useScore = useState;\n',
        },
        {"framework-dependency": 2},
    ),
    (
        "モジュールの内部と、他 feature の内部を読むのは公開 API の迂回",
        {
            "src/domains/money/index.ts": 'export { Money } from "./money";\n',
            "src/domains/money/money.ts": MONEY,
            "src/domains/money/__tests__/money.normal.test.ts": 'import { Money } from "../money";\ntest("金額を作れる", () => { expect(Money.create(1).amount).toBe(1); });\n',
            "src/services/total/index.ts": 'import { Money } from "@/src/domains/money/money";\nexport const total = Money;\n',
            "src/features/score/index.ts": 'export { label } from "./utils/label";\n',
            "src/features/score/utils/label.ts": 'export const label = "点数";\n',
            "src/features/match/index.ts": 'import { label } from "@/src/features/score/utils/label";\nimport { label as viaIndex } from "@/src/features/score";\nexport const match = [label, viaIndex];\n',
        },
        {"deep-import": 2},
    ),
    (
        "ファイル単位の循環は 1 つの輪を 1 件と数える",
        {
            "src/features/score/a.ts": 'import { b } from "./b";\nexport const a = b;\n',
            "src/features/score/b.ts": 'import { c } from "./c";\nexport const b = c;\n',
            "src/features/score/c.ts": 'import { a } from "./a";\nexport const c = a;\n',
            "src/features/score/d.ts": 'import { a } from "./a";\nexport const d = a;\n',
        },
        {"import-cycle": 1},
    ),
    (
        "ドメインの型だけのファイルと、1 フォルダに 2 つ以上の実装ファイル",
        {
            "src/domains/tile/index.ts": 'export type { Tile } from "./types";\n',
            "src/domains/tile/types.ts": "export type Tile = Readonly<{ id: string }>;\n",
            "src/domains/tile/suit.ts": "export const Suit = { all: [] };\n",
            "src/features/score/domains/fu/index.ts": "export interface Fu { value: number }\n",
            "src/types/ValueOf.ts": "export type ValueOf<T> = T[keyof T];\n",
        },
        {"domain-type-only-file": 2, "module-extra-implementation": 1},
    ),
    (
        "any と class。文字列やコメントの中の綴りは数えない",
        {
            "src/features/score/index.ts": (
                "export const a = (x: any): unknown => x as any;\n"
                "export class Score {}\n"
                'export const note = "x: any";\n'
                "// class Legacy {}\n"
                "export const company = 1;\n"
            ),
        },
        {"any": 1, "class": 1, "type-assertion": 1},
    ),
    (
        "domains・services の I/O。libs と feature では数えない",
        {
            "src/domains/money/index.ts": 'console.log("x");\n' + MONEY,
            "src/services/total/index.ts": 'export const total = () => fetch("/api");\n',
            "src/libs/storage/index.ts": 'export const read = () => localStorage.getItem("k");\n',
            "src/features/score/index.ts": 'export const log = () => console.log("x");\n',
        },
        {"domain-io": 2},
    ),
    (
        "JSON の解釈と組み立ては libs の外で数える。テストの共有物とテストは数えない",
        {
            "src/cli/calc.ts": 'export const main = (text: string) => JSON.stringify(JSON.parse(text));\n',
            "src/domains/money/index.ts": "export const toJson = (m: number) => JSON.stringify({ m });\n",
            "src/libs/json/index.ts": "export const parseJson = (text: string) => JSON.parse(text);\n",
            "src/services/total/__tests__/fixture.ts": 'export const input = JSON.parse("{}");\n',
            "src/services/total/__tests__/total.normal.test.ts": 'test("a", () => { expect(JSON.stringify({})).toBe("{}"); });\n',
        },
        {"json-outside-libs": 2},
    ),
    (
        "許されていないランタイム依存。devDependencies は数えない",
        {
            "package.json": '{"dependencies": {"next": "1", "react": "1", "riichi": "1", "zod": "1"}, "devDependencies": {"vitest": "1"}}',
            "src/features/score/index.ts": "export const a = 1;\n",
        },
        {"runtime-dependency": 2},
    ),
    (
        "検査の抑制コメントとフックの抜け道",
        {
            "src/features/score/index.ts": "// eslint-disable-next-line no-console\nexport const a = 1;\n// @ts-expect-error\nexport const b: number = a;\n",
            "src/features/score/__tests__/score.normal.test.ts": '// @test-rules-disable no-describe\ntest("a", () => {});\n',
        },
        {"suppression": 3},
    ),
    (
        "テストの @ts-expect-error は型が弾くことの確認なので数えない",
        {
            "src/features/score/__tests__/score.type.test.ts": '// @ts-expect-error 数値は渡せない\ntest("a", () => {});\n',
        },
        {},
    ),
    (
        "テストの describe・名前・置き場所",
        {
            "src/domains/money/index.ts": MONEY,
            "src/domains/money/__tests__/money.test.ts": 'describe("Money", () => { it("a", () => {}); });\n',
            "src/domains/money/money.normal.test.ts": 'test("a", () => {});\n',
        },
        {"test-describe": 1, "test-file-name": 1, "test-location": 1},
    ),
    (
        "モジュールの差し替えは全域、スパイ・スタブは domains・services のテストだけ数える",
        {
            "src/domains/money/index.ts": MONEY,
            "src/domains/money/__tests__/money.normal.test.ts": 'test("a", () => { const f = vi.fn(); vi.spyOn(console, "log"); });\n',
            "src/features/score/__tests__/score.normal.test.tsx": 'vi.mock("@/src/services/total");\ntest("a", () => { const f = vi.fn(); expect(f).toHaveBeenCalledWith(1); });\n',
        },
        {"test-module-mock": 1, "test-double-in-domain": 1, "test-interaction-assert": 1},
    ),
    (
        "ドメインの破壊的メソッドと let。feature とテストでは数えない",
        {
            "src/domains/hand/index.ts": (
                "export type Hand = Readonly<{ tiles: readonly string[] }>;\n"
                "export const Hand = {\n"
                "  add(hand: string[], tile: string) { hand.push(tile); return hand.sort(); },\n"
                "  count(hand: Hand) { let n = 0; n += hand.tiles.length; return n; },\n"
                "  sorted(hand: Hand) { return hand.tiles.toSorted(); },\n"
                "};\n"
            ),
            "src/domains/hand/__tests__/hand.normal.test.ts": 'test("a", () => { let x = [1]; x.push(2); });\n',
            "src/features/score/index.ts": "export const a = () => { let x = [1]; x.push(2); return x; };\n",
        },
        {"domain-mutating-call": 1, "domain-let": 1},
    ),
    (
        "テストの条件分岐と beforeEach",
        {
            "src/features/score/__tests__/score.normal.test.ts": (
                "let hand;\nbeforeEach(() => { hand = 1; });\n"
                'test("a", () => { if (hand) { expect(hand).toBe(1); } });\n'
            ),
        },
        {"test-conditional": 1, "test-before-each": 1},
    ),
    (
        "utils はフラットな PascalCase のファイル。テストは __tests__ にまとめてよい",
        {
            "src/utils/ArrayEx.ts": "export const ArrayEx = {};\n",
            "src/utils/__tests__/ArrayEx.normal.test.ts": 'test("a", () => {});\n',
            "src/utils/array.ts": "export const array = {};\n",
            "src/utils/string/index.ts": "export const StringEx = {};\n",
            "src/features/score/utils/label.ts": 'export const label = "点数";\n',
        },
        {"utils-form": 2},
    ),
    (
        "モジュールフォルダはケバブケース。同じフォルダは 1 件と数え、__tests__ と app は見ない",
        {
            "src/domains/winContext/index.ts": MONEY,
            "src/domains/winContext/__tests__/money.normal.test.ts": 'test("a", () => {});\n',
            "src/domains/win-context/index.ts": MONEY,
            "src/features/score/components/ScoreView/index.tsx": "export const ScoreView = () => <p />;\n",
            "app/(match)/page.tsx": "export default function Page() { return <p />; }\n",
        },
        {"folder-name": 2},
    ),
    (
        "600 行を超える .tsx。空行とコメントは数えず、.ts は見ない",
        {
            "src/features/score/components/long-view.tsx": "const a = 1;\n" * 601,
            "src/features/score/components/short-view.tsx": "const a = 1;\n\n// note\n" * 600,
            "src/features/score/long.ts": "const a = 1;\n" * 700,
        },
        {"long-tsx": 1},
    ),
    (
        "throw と try は libs の外で数える。Context のアクセサフックと Result の unwrap は数えない",
        {
            "src/domains/tile/index.ts": 'export const parse = (text: string) => { if (!text) { throw new Error("x"); } return text; };\n',
            "src/services/total/index.ts": "export const total = (f: () => number) => {\n  try {\n    return f();\n  } catch {\n    return 0;\n  }\n};\n",
            "src/libs/json/index.ts": "export const parseJson = (text: string) => { try { return JSON.parse(text); } catch { return undefined; } };\n",
            "src/features/score/hooks/useScore.ts": 'export const useScore = () => { const value = useContext(ScoreContext); if (!value) { throw new Error("Provider"); } return value; };\n',
            "src/utils/Result.ts": 'export const Result = { unwrap: (ok: boolean) => { if (!ok) { throw new Error("x"); } } };\n',
            "src/domains/tile/__tests__/tile.edge.test.ts": 'test("a", () => { expect(() => { throw new Error("x"); }).toThrow(); });\n',
            "src/domains/tile/__tests__/tile-fixture.ts": 'export const must = (ok: boolean) => { if (!ok) { throw new Error("x"); } };\n',
        },
        {"exception": 2},
    ),
    (
        "引数が 4 つ以上の関数。分割代入と型引数の中のカンマは数えない",
        {
            "src/features/score/index.ts": (
                "export function four(a: number, b: number, c: number, d: number) { return a + b + c + d; }\n"
                "export const arrow = (a: number, b: number, c: number, d: number): number => a + b + c + d;\n"
                "export const Score = {\n"
                "  method(\n    a: number,\n    b: number,\n    c: number,\n    d: number,\n  ): number {\n    return a + b + c + d;\n  },\n"
                "  three(a: Map<string, number>, { b, c, d }: Props, e: [number, number]) { return [a, b, c, d, e]; },\n"
                "};\n"
                "export const call = () => four(1, 2, 3, 4);\n"
                "if (four(1, 2, 3, 4)) { call(); }\n"
            ),
        },
        {"param-count": 3},
    ),
    (
        "コンポーネントの戻り値の ReactNode。props の型の ReactNode は数えない",
        {
            "src/features/score/components/score-view.tsx": (
                "export const ScoreView = ({ children }: { children: ReactNode }): ReactNode => <p>{children}</p>;\n"
                "export const Other = (): ReactElement => <p />;\n"
            ),
        },
        {"react-node-return": 1},
    ),
    (
        "名前の規約。宣言の行だけを数え、使う側は数えない",
        {
            "src/domains/tile/index.ts": (
                "export const MAX_TILES = 136;\n"
                "export const MaxHand = 14;\n"
                "export const parseAndValidate = (text: string) => text;\n"
                "export type TileInfo = Readonly<{ id: string }>;\n"
                "export const Tile = {\n"
                "  ofText(text: string) { return text; },\n"
                "  create(text: string) { return parseAndValidate(text).slice(0, MAX_TILES); },\n"
                "  sortAndGroup(tiles: readonly string[]) {\n    return tiles;\n  },\n"
                "};\n"
                "export const Android = Tile;\n"
            ),
            "src/domains/tile/__tests__/helpers.ts": "export const tile = 1;\n",
        },
        {"constant-case": 1, "and-name": 2, "generic-name": 2, "of-method": 1},
    ),
    (
        "undefined を返す戻り値の型・switch の default・型の言い換え・doc の無い宣言",
        {
            "src/features/score/index.ts": (
                'import { a as renamed } from "./a";\n'
                "/** 点数を探す。 */\n"
                "export const find = (id: string, fallback: number | undefined): number | undefined => fallback;\n"
                "export const label = (kind: string): string => {\n"
                "  switch (kind) {\n"
                '    case "a":\n      return "A";\n'
                '    default:\n      return "";\n'
                "  }\n"
                "};\n"
                "/** 別名。 */\n"
                "export const cast = renamed as unknown as number;\n"
                'export const fixed = ["a"] as const;\n'
            ),
            "src/features/score/a.ts": "/** a */\nexport const a = 1;\n",
        },
        {"nullable-return": 1, "switch-default": 1, "type-assertion": 1, "undocumented-export": 2},
    ),
    (
        "useEffect とグローバルなリスナー",
        {
            "src/features/score/hooks/useScore.ts": (
                "export const useScore = () => {\n"
                "  useEffect(() => {\n"
                '    window.addEventListener("resize", () => {});\n'
                "  }, []);\n"
                "};\n"
            ),
        },
        {"use-effect": 1, "global-listener": 1},
    ),
]


def place(root: Path, files: dict[str, str]) -> None:
    """ケースのファイルを一時フォルダへ置く。

    @param root 置き先のフォルダ
    @param files パスから中身への対応
    """
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def counted(files: dict[str, str]) -> dict[str, int]:
    """ケース 1 つを採点して、種別ごとの件数を返す。

    @param files パスから中身への対応
    @returns 違反と参考値を合わせた、種別から件数への対応
    """
    with tempfile.TemporaryDirectory() as folder:
        place(Path(folder), files)
        result = score(Path(folder))
    entries = {**result["violations"], **result["indicators"]}
    return {kind: entry["count"] for kind, entry in entries.items()}


def main() -> int:
    """判定表を流して、食い違いを報告する。

    @returns 終了コード。食い違いがあれば 1
    """
    all_kinds = (*VIOLATION_KINDS, *INDICATOR_KINDS)
    failed = 0
    for name, files, expected in CASES:
        actual = counted(files)
        mismatches = [
            f"{kind}: 期待 {expected.get(kind, 0)} / 実際 {actual[kind]}"
            for kind in all_kinds
            if actual[kind] != expected.get(kind, 0)
            and (kind in expected or kind not in UNCHECKED_UNLESS_EXPECTED)
        ]
        print(f"{'NG' if mismatches else 'ok'}  {name}")
        for mismatch in mismatches:
            print(f"      {mismatch}")
        failed += bool(mismatches)
    uncovered = [kind for kind in all_kinds if not any(kind in expected for _, _, expected in CASES)]
    for kind in uncovered:
        print(f"NG  {kind} を 1 件以上期待するケースが表に無い")
    return 1 if failed or uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
