#!/usr/bin/env python3
"""被験エージェントが書いたコードの、実装規約からの外れを数える。

coding-task-lab の `rules/` のうち、**ソースを読むだけで判定できるもの**を種別ごとに数える。
機能の正しさ（`expected_data_sample.json` との突き合わせ）と、lint / typecheck / test / build の
成否はここでは見ない。

数えるものは 2 つに分けて出す。

- 違反（`VIOLATION_KINDS`）: 規約が禁止と書いていて、1 件が 1 つの違反に対応するもの
- 参考値（`INDICATOR_KINDS`）: 規約が「可能な限り避ける」「最終手段」と書くものの使用回数。
  1 件が違反とは限らないので、合計にも密度にも入れない

正規表現で数える種別（`LINE_PATTERNS` と `suppression`）の 1 件は 1 行で、同じ行に 2 つあっても
1 件と数える。型は解かないので、`domain-mutating-call` は引数ではなく手元で作った配列への
`push` も数える（参考値に置いている理由）。

**重み付けした 1 つの点数は出さない。** 種別ごとの重さは測定の狙いによって変わるので、
件数と 1000 行あたりの密度までを出し、比べ方は読む側が決める。

使い方:
    convention_score.py <プロジェクトのフォルダ> [--json]

    例: convention_score.py ../coding-task-lab/mahjong --json > phase1-conventions.json

終了コードは、採点できたら違反の有無によらず 0、フォルダが無ければ 2。
"""

import json
import os
import re
import sys
from pathlib import Path

SOURCE_SUFFIXES = (".ts", ".tsx")
SKIPPED_FOLDERS = {"node_modules", ".next", "out", "build", "dist", "coverage", ".git"}
INDEX_NAMES = ("index.ts", "index.tsx")
TEST_FILE = re.compile(r"\.test\.tsx?$")
# `<対象>.<カテゴリ>.test.ts(x)` の形。
TEST_FILE_NAME = re.compile(r"^[^.]+\.[^.]+\.test\.tsx?$")
IMPORT_SPECIFIER = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*["']([^"']+)["']""")
ALIAS = "@/"
MAX_EXAMPLES = 5

# 層の名前。`src/` の直下、またはプロジェクト直下（Next.js の `app/`）の最初のフォルダ名で決める。
LAYERS = ("app", "features", "domains", "services", "components", "hooks", "libs", "utils", "types")

# 層ごとの、import してはならない層（`rules/architecture.md`「依存方向のルール」）。
FORBIDDEN_LAYERS = {
    "domains": {"services", "features", "app"},
    "services": {"features", "app", "components", "hooks", "libs"},
    "features": {"app"},
    "components": {"domains", "services", "features", "app"},
    "hooks": {"domains", "services", "features", "app"},
    "utils": {"domains", "services", "features", "app"},
    "types": {"domains", "services", "features", "app"},
    "libs": {"domains", "services", "features", "app", "components", "hooks", "utils"},
}

# React / Next.js に依存してはならない層。
FRAMEWORK_FREE_LAYERS = {"domains", "services"}
FRAMEWORK_PACKAGE = re.compile(r"^(react|react-dom|next)(/|$)")

# 公開 API（`index.ts`）を持つモジュールフォルダが置かれる層。
MODULE_LAYERS = {"domains", "services", "features"}

# ランタイム依存として許されるパッケージ（`AGENTS.md`「外部ランタイム依存: 原則禁止」）。
ALLOWED_DEPENDENCIES = {"next", "react", "react-dom"}

VIOLATION_KINDS = (
    "layer-direction",
    "framework-dependency",
    "deep-import",
    "import-cycle",
    "domain-type-only-file",
    "module-extra-implementation",
    "any",
    "class",
    "domain-io",
    "runtime-dependency",
    "suppression",
    "test-describe",
    "test-file-name",
    "test-location",
    "test-module-mock",
    "test-double-in-domain",
)

INDICATOR_KINDS = (
    "domain-mutating-call",
    "domain-let",
    "test-conditional",
    "test-before-each",
    "test-interaction-assert",
    "use-effect",
    "global-listener",
)

# 1 行に対する正規表現で数える種別。値は（対象を絞る関数の名前, パターン）。
LINE_PATTERNS = {
    "any": ("is_source", re.compile(r"(?::\s*any\b|\bas\s+any\b|<\s*any\b|\bany\s*\[\]|,\s*any\s*>)")),
    "class": ("is_source", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+\w")),
    "domain-io": (
        "is_pure_layer",
        re.compile(r"\b(?:console\.\w+|localStorage\b|sessionStorage\b|fetch\s*\(|process\.(?:stdin|stdout|env))"),
    ),
    "test-describe": ("is_test", re.compile(r"\bdescribe(?:\.\w+)?\s*\(")),
    "test-module-mock": ("is_test", re.compile(r"\bvi\.(?:mock|doMock)\s*\(")),
    "test-double-in-domain": ("is_pure_layer_test", re.compile(r"\bvi\.(?:fn|spyOn|stubGlobal)\s*\(")),
    "domain-mutating-call": (
        "is_domain_source",
        re.compile(r"\.(?:push|pop|shift|unshift|splice|sort|reverse|fill)\s*\("),
    ),
    "domain-let": ("is_domain_source", re.compile(r"\blet\s+[\w{\[]")),
    "test-conditional": ("is_test", re.compile(r"\b(?:if|switch)\s*\(")),
    "test-before-each": ("is_test", re.compile(r"\b(?:beforeEach|beforeAll)\s*\(")),
    "test-interaction-assert": ("is_test", re.compile(r"\.toHaveBeenCalled\w*\s*\(")),
    "use-effect": ("is_source", re.compile(r"\buse(?:Layout)?Effect\s*\(")),
    "global-listener": ("is_source", re.compile(r"\b(?:document|window)\.addEventListener\s*\(")),
}

# コメントに書かれる検査の抑制と、フックの抜け道。コメントを落とす前の行で数える。
SUPPRESSION = re.compile(
    r"(?:eslint-disable|biome-ignore|@ts-ignore|@ts-expect-error|@ts-nocheck|@lint-suppress-ok|@test-rules-disable)"
)

TYPE_DECLARATION = re.compile(r"^\s*export\s+(?:type\s+\w+\s*[=<]|interface\s+\w)", re.M)
VALUE_DECLARATION = re.compile(r"^\s*export\s+(?:const|function|async\s+function|default)\b", re.M)
TEST_CASE = re.compile(r"^\s*(?:test|it)(?:\.\w+)*\s*\(", re.M)


def strip_comments_and_strings(text: str) -> str:
    """コメントと文字列の中身を空白へ置き換える。

    行番号を保つために改行は残す。テンプレートリテラルの `${}` の中も文字列として落とす。

    @param text ソースの全文
    @returns コードの部分だけが残った、行数の同じ文字列
    """
    pattern = re.compile(
        r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`""", re.S
    )
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def layer_of(path: str) -> str | None:
    """そのファイルが属する層を求める。

    @param path プロジェクトのフォルダから見たパス
    @returns 層の名前。`src/cli/` のように規約が層として挙げていない場所なら `None`
    """
    parts = path.split("/")
    rest = parts[1:] if parts[0] == "src" else parts
    return rest[0] if len(rest) > 1 and rest[0] in LAYERS else None


def is_test(path: str) -> bool:
    """@returns テストファイル（`*.test.ts(x)`）なら真"""
    return bool(TEST_FILE.search(path))


def is_source(path: str) -> bool:
    """@returns テストではない実装ファイルなら真"""
    return not is_test(path)


def in_domains(path: str) -> bool:
    """@returns `src/domains/` か、feature の中の `domains/` の下にあれば真"""
    return "domains" in path.split("/")[:-1]


def is_domain_source(path: str) -> bool:
    """@returns ドメインの実装ファイルなら真"""
    return in_domains(path) and is_source(path)


def in_pure_layer(path: str) -> bool:
    """@returns I/O を持てない場所（ドメインか `src/services/`）なら真"""
    return in_domains(path) or layer_of(path) == "services"


def is_pure_layer(path: str) -> bool:
    """@returns I/O を持てない場所の実装ファイルなら真"""
    return in_pure_layer(path) and is_source(path)


def is_pure_layer_test(path: str) -> bool:
    """@returns I/O を持てない場所のテストファイルなら真"""
    return in_pure_layer(path) and is_test(path)


FILE_FILTERS = {
    f.__name__: f for f in (is_test, is_source, is_domain_source, is_pure_layer, is_pure_layer_test)
}


def source_files(root: Path) -> list[str]:
    """採点の対象になるファイルを集める。

    @param root プロジェクトのフォルダ
    @returns `root` から見た `/` 区切りのパスの並び。型宣言（`*.d.ts`）・設定ファイル
        （直下の `*.config.ts` など）・依存やビルドの出力は除く
    """
    found = []
    for folder, folders, names in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in SKIPPED_FOLDERS)
        relative = Path(folder).relative_to(root).as_posix()
        if relative == ".":
            continue
        found += [
            f"{relative}/{name}"
            for name in sorted(names)
            if name.endswith(SOURCE_SUFFIXES) and not name.endswith(".d.ts")
        ]
    return found


def resolve_import(specifier: str, importer: str, files: set[str]) -> str | None:
    """import 先のファイルを求める。

    @param specifier import に書かれている綴り（`@/` はプロジェクトのフォルダを指す）
    @param importer それを書いているファイルのパス
    @param files 実在するファイルのパスの集合
    @returns 解決できたファイルのパス。外部パッケージや実在しない綴りなら `None`
    """
    if specifier.startswith(ALIAS):
        base = specifier[len(ALIAS) :]
    elif specifier.startswith("."):
        base = os.path.normpath(f"{os.path.dirname(importer)}/{specifier}").replace(os.sep, "/")
    else:
        return None
    candidates = (base, f"{base}.ts", f"{base}.tsx", *(f"{base}/{name}" for name in INDEX_NAMES))
    return next((c for c in candidates if c in files), None)


def feature_of(path: str) -> str | None:
    """@returns そのファイルが属する feature の名前。feature の外なら `None`"""
    parts = path.split("/")
    rest = parts[1:] if parts[0] == "src" else parts
    return rest[1] if len(rest) > 2 and rest[0] == "features" else None


def is_deep_import(importer: str, target: str, files: set[str]) -> bool:
    """公開 API を迂回した import かを判定する。

    他の feature は、その feature の直下の `index.ts` だけを読める。それ以外のモジュール
    フォルダは、`index.ts` を持つフォルダの外から、中の `index.ts` 以外を読むと迂回になる。

    @param importer import を書いているファイル
    @param target 解決済みの import 先
    @param files 実在するファイルのパスの集合
    @returns 迂回していれば真
    """
    target_feature = feature_of(target)
    crosses_feature = target_feature is not None and target_feature != feature_of(importer)
    if crosses_feature:
        feature_root = target[: target.index(f"features/{target_feature}/")] + f"features/{target_feature}"
        return os.path.dirname(target) != feature_root or os.path.basename(target) not in INDEX_NAMES
    if layer_of(target) not in MODULE_LAYERS or os.path.basename(target) in INDEX_NAMES:
        return False
    folder = os.path.dirname(target)
    while layer_of(f"{folder}/x") in MODULE_LAYERS:
        has_index = any(f"{folder}/{name}" in files for name in INDEX_NAMES)
        if has_index and not importer.startswith(f"{folder}/"):
            return True
        folder = os.path.dirname(folder)
    return False


def count_cycles(edges: dict[str, set[str]]) -> list[str]:
    """ファイル単位の import の循環を数える。

    @param edges ファイルから、それが import しているファイルの集合への対応
    @returns 循環 1 つ（2 ファイル以上の強連結成分）ごとの説明
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[str] = []

    for start in sorted(edges):
        if start in index:
            continue
        # 再帰の深さがファイル数に比例するので、明示のスタックで辿る。
        work = [(start, iter(sorted(edges[start])))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            node, successors = work[-1]
            successor = next((s for s in successors if s not in index or s in on_stack), None)
            if successor is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component = _pop_component(stack, on_stack, node)
                    if len(component) > 1:
                        cycles.append(" → ".join(sorted(component)))
            elif successor in index:
                low[node] = min(low[node], index[successor])
            else:
                index[successor] = low[successor] = len(index)
                stack.append(successor)
                on_stack.add(successor)
                work.append((successor, iter(sorted(edges.get(successor, ())))))
    return cycles


def _pop_component(stack: list[str], on_stack: set[str], root: str) -> list[str]:
    """スタックの上から `root` までを 1 つの強連結成分として取り出す。

    @param stack 辿っている途中のファイルの並び（取り出した分だけ縮む）
    @param on_stack `stack` に載っているファイルの集合（同じく縮む）
    @param root 成分の根
    @returns 成分に属するファイルの並び
    """
    component = []
    while True:
        node = stack.pop()
        on_stack.discard(node)
        component.append(node)
        if node == root:
            return component


def scan_imports(sources: dict[str, str]) -> dict[str, list[str]]:
    """import から読める違反を集める。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `layer-direction` / `framework-dependency` / `deep-import` / `import-cycle` ごとの説明
    """
    files = set(sources)
    found: dict[str, list[str]] = {
        "layer-direction": [],
        "framework-dependency": [],
        "deep-import": [],
        "import-cycle": [],
    }
    edges: dict[str, set[str]] = {path: set() for path in files}
    for importer in sorted(files):
        layer = layer_of(importer)
        code_without_comments = re.sub(r"//[^\n]*|/\*.*?\*/", "", sources[importer], flags=re.S)
        for specifier in IMPORT_SPECIFIER.findall(code_without_comments):
            if layer in FRAMEWORK_FREE_LAYERS and FRAMEWORK_PACKAGE.match(specifier):
                found["framework-dependency"].append(f"{importer} → {specifier}")
            target = resolve_import(specifier, importer, files)
            if target is None:
                continue
            edges[importer].add(target)
            # `rules/testing.md` は外部 I/O をフェイクで置き換えることを認めていて、その置き場は libs。
            reads_fake_from_test = "__tests__" in importer.split("/") and layer_of(target) == "libs"
            if layer_of(target) in FORBIDDEN_LAYERS.get(layer, ()) and not reads_fake_from_test:
                found["layer-direction"].append(f"{importer} → {target}")
            if is_deep_import(importer, target, files):
                found["deep-import"].append(f"{importer} → {target}")
    found["import-cycle"] = count_cycles(edges)
    return found


def scan_lines(sources: dict[str, str]) -> dict[str, list[str]]:
    """1 行ごとの正規表現で読める違反と参考値を集める。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `LINE_PATTERNS` の種別と `suppression` ごとの `<パス>:<行番号>`
    """
    found: dict[str, list[str]] = {kind: [] for kind in (*LINE_PATTERNS, "suppression")}
    for path in sorted(sources):
        raw_lines = sources[path].split("\n")
        code_lines = strip_comments_and_strings(sources[path]).split("\n")
        found["suppression"] += [
            f"{path}:{number}" for number, line in enumerate(raw_lines, 1) if SUPPRESSION.search(line)
        ]
        for kind, (filter_name, pattern) in LINE_PATTERNS.items():
            if not FILE_FILTERS[filter_name](path):
                continue
            found[kind] += [
                f"{path}:{number}" for number, line in enumerate(code_lines, 1) if pattern.search(line)
            ]
    return found


def scan_layout(sources: dict[str, str]) -> dict[str, list[str]]:
    """ファイルの置き場所と名前から読める違反を集める。

    `module-extra-implementation` は `domains/` と `src/services/` の下だけを見る。feature の
    `components/` のように、規約がモジュールフォルダかどうかを明示していない場所は数えない。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `domain-type-only-file` / `module-extra-implementation` / `test-file-name` /
        `test-location` ごとの説明
    """
    found: dict[str, list[str]] = {
        "domain-type-only-file": [],
        "module-extra-implementation": [],
        "test-file-name": [],
        "test-location": [],
    }
    implementations: dict[str, list[str]] = {}
    for path in sorted(sources):
        folder, name = os.path.split(path)
        if is_test(path):
            if not TEST_FILE_NAME.match(name):
                found["test-file-name"].append(path)
            if os.path.basename(folder) != "__tests__":
                found["test-location"].append(path)
            continue
        if "__tests__" in path.split("/") or not in_pure_layer(path):
            continue
        code = strip_comments_and_strings(sources[path])
        declares_only_types = TYPE_DECLARATION.search(code) and not VALUE_DECLARATION.search(code)
        if in_domains(path) and declares_only_types:
            found["domain-type-only-file"].append(path)
        if name not in INDEX_NAMES:
            implementations.setdefault(folder, []).append(name)
    found["module-extra-implementation"] = [
        f"{folder}: {', '.join(names)}" for folder, names in implementations.items() if len(names) > 1
    ]
    return found


def scan_dependencies(root: Path) -> list[str]:
    """許されていないランタイム依存を集める。

    @param root プロジェクトのフォルダ
    @returns `package.json` の `dependencies` のうち、許されていないパッケージの名前。
        `package.json` が無い・読めないなら空
    """
    manifest = root / "package.json"
    if not manifest.is_file():
        return []
    try:
        dependencies = json.loads(manifest.read_text(encoding="utf-8")).get("dependencies", {})
    except json.JSONDecodeError:
        return []
    return sorted(name for name in dependencies if name not in ALLOWED_DEPENDENCIES)


def measure_size(sources: dict[str, str]) -> dict[str, int]:
    """密度の分母になる規模を測る。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns 実装のファイル数・行数（空行とコメントを除く）・いちばん長いファイルの行数と、
        テストのファイル数・ケース数
    """
    tests = [path for path in sources if is_test(path)]
    lines_per_file = [
        sum(1 for line in strip_comments_and_strings(sources[path]).split("\n") if line.strip())
        for path in sources
        if is_source(path)
    ]
    return {
        "sourceFiles": len(lines_per_file),
        "sourceLines": sum(lines_per_file),
        "longestSourceFileLines": max(lines_per_file, default=0),
        "testFiles": len(tests),
        "testCases": sum(len(TEST_CASE.findall(strip_comments_and_strings(sources[p]))) for p in tests),
    }


def score(root: Path) -> dict:
    """プロジェクト 1 つを採点する。

    @param root プロジェクトのフォルダ
    @returns 規模・違反・参考値・違反の合計・実装 1000 行あたりの違反の件数。実装が 0 行なら
        密度は `None`
    """
    sources = {path: (root / path).read_text(encoding="utf-8") for path in source_files(root)}
    found = {
        **scan_imports(sources),
        **scan_lines(sources),
        **scan_layout(sources),
        "runtime-dependency": scan_dependencies(root),
    }
    size = measure_size(sources)
    total = sum(len(found[kind]) for kind in VIOLATION_KINDS)

    def summarize(kinds: tuple[str, ...]) -> dict:
        return {kind: {"count": len(found[kind]), "examples": found[kind][:MAX_EXAMPLES]} for kind in kinds}

    return {
        "size": size,
        "violations": summarize(VIOLATION_KINDS),
        "indicators": summarize(INDICATOR_KINDS),
        "violationTotal": total,
        "violationsPerKloc": round(total * 1000 / size["sourceLines"], 2) if size["sourceLines"] else None,
    }


def print_report(result: dict) -> None:
    """採点の結果を人が読む形で出力する。

    @param result `score` の戻り値
    """
    size = result["size"]
    print(
        f"規模: 実装 {size['sourceFiles']} ファイル / {size['sourceLines']} 行"
        f"（最長 {size['longestSourceFileLines']} 行）、"
        f"テスト {size['testFiles']} ファイル / {size['testCases']} ケース"
    )
    for title, key in (("違反", "violations"), ("参考値", "indicators")):
        print(f"\n{title}")
        for kind, entry in result[key].items():
            print(f"  {kind:<28} {entry['count']}")
            for example in entry["examples"]:
                print(f"      {example}")
    print(f"\n違反の合計: {result['violationTotal']}（実装 1000 行あたり {result['violationsPerKloc']}）")


def main(arguments: list[str]) -> int:
    """コマンドラインの引数を解いて採点する。

    @param arguments プログラム名を除いた引数
    @returns 終了コード。フォルダの指定が無い・存在しないなら 2
    """
    paths = [a for a in arguments if not a.startswith("--")]
    if len(paths) != 1 or not Path(paths[0]).is_dir():
        print(__doc__)
        return 2
    result = score(Path(paths[0]))
    if "--json" in arguments:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
