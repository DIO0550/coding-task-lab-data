#!/usr/bin/env python3
"""`convention_score.py` の判定表。

一時フォルダへ小さなプロジェクトを置いて採点し、種別ごとの件数が期待どおりかを確かめる。
期待に書いていない種別は 0 件であることも確かめるので、数えすぎも落ちる。

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
            "src/domains/money/index.ts": 'import { total } from "@/src/services/total";\nimport { ScoreView } from "@/src/features/score";\n' + MONEY,
            "src/services/total/index.ts": "export const total = 1;\n",
            "src/features/score/index.ts": "export const ScoreView = 1;\n",
        },
        {"layer-direction": 2},
    ),
    (
        "utils からドメイン、libs から utils、services から libs への import は向きの違反",
        {
            "src/utils/ArrayEx.ts": "import { Money } from '../domains/money';\nexport const ArrayEx = { Money };\n",
            "src/libs/storage/index.ts": 'import { ArrayEx } from "@/src/utils/ArrayEx";\nexport const storage = ArrayEx;\n',
            "src/services/total/index.ts": 'import { storage } from "@/src/libs/storage";\nexport const total = storage;\n',
            "src/domains/money/index.ts": MONEY,
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
        {"any": 1, "class": 1},
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
