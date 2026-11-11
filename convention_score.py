#!/usr/bin/env python3
"""被験エージェントが書いたコードの、実装規約からの外れを数える。

coding-task-lab の `rules/`（版 0.5.0 の規約）のうち、**ソースを読むだけで判定できるもの**を
種別ごとに数える。それより前の版の成果物を読ませると、当時は無かった規約でも数える。
機能の正しさ（`expected_data_sample.json` との突き合わせ）と、lint / typecheck / test / build の
成否はここでは見ない。

数えるものは 2 つに分けて出す。

- 違反（`VIOLATION_KINDS`）: 規約が禁止と書いていて、1 件が 1 つの違反に対応するもの
- 参考値（`INDICATOR_KINDS`）: 規約が「可能な限り避ける」「最終手段」と書くものの使用回数。
  1 件が違反とは限らないので、合計にも密度にも入れない

正規表現で数える種別（`LINE_PATTERNS` と `suppression`）の 1 件は 1 行で、同じ行に 2 つあっても
1 件と数える。型は解かないので、`domain-mutating-call` は引数ではなく手元で作った配列への
`push` も数える（参考値に置いている理由）。

名前を見る種別（`constant-case` / `and-name` / `generic-name` / `of-method`）が見るのは宣言の行だけで、
使っている側の行は数えない。

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
LAYERS = ("app", "cli", "features", "domains", "services", "components", "hooks", "libs", "utils", "types")

# 層ごとの、import してはならない層（`rules/architecture.md`「依存方向のルール」）。
# 入口の層（`app` / `cli`）は、どの層からも import できない。
ENTRY_LAYERS = {"app", "cli"}
FORBIDDEN_LAYERS = {
    "cli": {"app", "features", "components", "hooks"},
    "app": {"cli"},
    "domains": {"services", "features", "libs"} | ENTRY_LAYERS,
    "services": {"features", "components", "hooks", "libs"} | ENTRY_LAYERS,
    "features": ENTRY_LAYERS,
    "components": {"domains", "services", "features"} | ENTRY_LAYERS,
    "hooks": {"domains", "services", "features"} | ENTRY_LAYERS,
    "utils": {"domains", "services", "features"} | ENTRY_LAYERS,
    "types": {"domains", "services", "features"} | ENTRY_LAYERS,
    "libs": {"services", "features", "components", "hooks"} | ENTRY_LAYERS,
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
    "utils-form",
    "folder-name",
    "long-tsx",
    "any",
    "class",
    "exception",
    "param-count",
    "react-node-return",
    "constant-case",
    "and-name",
    "generic-name",
    "of-method",
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
    "nullable-return",
    "switch-default",
    "type-assertion",
    "undocumented-export",
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
    "exception": ("is_source_outside_libs", re.compile(r"\bthrow\b|\btry\s*\{")),
    "react-node-return": ("is_source", re.compile(r"\)\s*:\s*(?:React\.)?ReactNode\b")),
    "constant-case": ("is_source", re.compile(r"\bconst\s+[A-Z][A-Z0-9_]+\b(?=\s*[:=])")),
    "and-name": (
        "is_source",
        re.compile(
            r"\b(?:function|const|let|type|interface)\s+\w*[a-z0-9]And[A-Z]\w*"
            r"|^\s*(?:async\s+)?\w*[a-z0-9]And[A-Z]\w*\s*(?:<[^>]*>)?(?:\([^)]*\)\s*(?::[^{]+)?\{\s*$|\s*:\s*(?:async\s*)?\()"
        ),
    ),
    "generic-name": (
        "is_source",
        re.compile(
            r"\b(?:function|const|type|interface)\s+\w*(?:Entry|Entries|Info|Data|Details?|Manager|Helpers?)\b"
        ),
    ),
    "of-method": (
        "is_source",
        re.compile(r"^\s*of(?:[A-Z]\w*)?\s*(?:<[^>]*>)?\s*[(:]|\b(?:function|const)\s+of[A-Z]\w*"),
    ),
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
    "nullable-return": ("is_source", re.compile(r"\)\s*:\s*[^=;{}()]*\|\s*(?:undefined|null)\b")),
    "switch-default": ("is_source", re.compile(r"^\s*default\s*:")),
    "type-assertion": ("is_source", re.compile(r"\bas\s+(?!const\b)[\w{\[(]")),
    "test-conditional": ("is_test", re.compile(r"\b(?:if|switch)\s*\(")),
    "test-before-each": ("is_test", re.compile(r"\b(?:beforeEach|beforeAll)\s*\(")),
    "test-interaction-assert": ("is_test", re.compile(r"\.toHaveBeenCalled\w*\s*\(")),
    "use-effect": ("is_source", re.compile(r"\buse(?:Layout)?Effect\s*\(")),
    "global-listener": ("is_source", re.compile(r"\b(?:document|window)\.addEventListener\s*\(")),
}

# その行を数えない条件。import / export の `as` は別名で、型の言い換えではない。
LINE_EXCLUSIONS = {
    "type-assertion": re.compile(r"^\s*(?:import\b|export\s*(?:type\s*)?[{*])|^\s*\w+\s+as\s+\w+\s*,?\s*$|\*\s+as\s+"),
}

# そのファイルでは数えない条件。Context のアクセサフックが Provider の外で投げるのは規約が認めている。
FILE_EXCLUSIONS = {
    "exception": re.compile(r"\buseContext\s*\("),
}

# コメントに書かれる検査の抑制と、フックの抜け道。コメントを落とす前の行で数える。
SUPPRESSION = re.compile(
    r"(?:eslint-disable|biome-ignore|@ts-ignore|@ts-nocheck|@lint-suppress-ok|@test-rules-disable)"
)
# テストでは、型が弾くことを固定する手段なので抑制に数えない。
TYPE_ERROR_EXPECTATION = re.compile(r"@ts-expect-error")

KEBAB_CASE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PASCAL_CASE_FILE = re.compile(r"^[A-Z][A-Za-z0-9]*\.tsx?$")
GENERIC_FILE_NAME = re.compile(
    r"(?:^|[-_])(?:helpers?|info|data|details?|manager|entry|entries)$|(?:Helpers?|Info|Data|Details?|Manager|Entry|Entries)$"
)
# テストの共有物を置くフォルダ。モジュールフォルダとしての名前の規則は掛けない。
TEST_FOLDERS = {"__tests__", "__fixtures__", "__mocks__"}
UNWRAP_OWNERS = {"Result.ts", "Option.ts"}
MAX_TSX_LINES = 600
MAX_PARAMETERS = 3
EXPORTED_DECLARATION = re.compile(r"^export\s+(?:const|function|async\s+function|type|interface)\s+\w+")
CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "await", "typeof", "new"}
FUNCTION_HEAD = re.compile(r"\bfunction\b\s*\*?\s*\w*\s*(?:<[^()]*>)?\s*$")
METHOD_HEAD = re.compile(r"(?:^|[\n;{},])\s*(?:(?:async|get|set)\s+)*(\w+)\s*(?:<[^()]*>)?\s*$")
ARROW_TAIL = re.compile(r"\s*(?::[^=;{}]*?)?\s*=>")
BODY_TAIL = re.compile(r"\s*(?::[^;=]*?)?\s*\{")

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
    @returns 層の名前。規約が層として挙げていない場所なら `None`
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


def is_source_outside_libs(path: str) -> bool:
    """@returns 例外を投げてはならない実装ファイルなら真。

    例外へ変換してよい境界（`libs/`）・テストの共有物・テスト用の `unwrap` を持つ雛形
    （`Result` / `Option`）は除く
    """
    is_unwrap_owner = layer_of(path) == "utils" and os.path.basename(path) in UNWRAP_OWNERS
    is_test_support = bool(TEST_FOLDERS & set(path.split("/")))
    return is_source(path) and layer_of(path) != "libs" and not (is_unwrap_owner or is_test_support)


FILE_FILTERS = {
    f.__name__: f
    for f in (is_test, is_source, is_domain_source, is_pure_layer, is_pure_layer_test, is_source_outside_libs)
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
        # 同じ先を型と値で 2 回 import していても、依存は 1 本と数える。
        for specifier in sorted(set(IMPORT_SPECIFIER.findall(code_without_comments))):
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
            f"{path}:{number}"
            for number, line in enumerate(raw_lines, 1)
            if SUPPRESSION.search(line) or (is_source(path) and TYPE_ERROR_EXPECTATION.search(line))
        ]
        for kind, (filter_name, pattern) in LINE_PATTERNS.items():
            excluded_file = kind in FILE_EXCLUSIONS and FILE_EXCLUSIONS[kind].search(sources[path])
            if excluded_file or not FILE_FILTERS[filter_name](path):
                continue
            excluded_line = LINE_EXCLUSIONS.get(kind)
            found[kind] += [
                f"{path}:{number}"
                for number, line in enumerate(code_lines, 1)
                if pattern.search(line) and not (excluded_line and excluded_line.search(line))
            ]
    return found


def paired_parentheses(code: str) -> list[tuple[int, int]]:
    """丸括弧の開きと閉じの位置を対にする。

    @param code コメントと文字列を落としたソース
    @returns （開きの位置, 閉じの位置）の並び。対にならない括弧は含めない
    """
    pairs = []
    opened: list[int] = []
    for position, character in enumerate(code):
        if character == "(":
            opened.append(position)
        elif character == ")" and opened:
            pairs.append((opened.pop(), position))
    return pairs


def count_parameters(parameters: str) -> int:
    """引数の並びに書かれた引数の数を数える。

    @param parameters 丸括弧の中身
    @returns いちばん外側のカンマで区切った、空でない項の数。分割代入は 1 つと数える
    """
    depth = 0
    count = 0
    has_content = False
    for position, character in enumerate(parameters):
        closes_generic = character == ">" and parameters[position - 1 : position] != "="
        if character in "([{<":
            depth += 1
        elif character in ")]}" or closes_generic:
            depth -= 1
        if character == "," and depth == 0:
            count += has_content
            has_content = False
        elif not character.isspace():
            has_content = True
    return count + has_content


def scan_signatures(sources: dict[str, str]) -> dict[str, list[str]]:
    """関数の宣言から読める違反を集める。

    丸括弧の対のうち、`function` に続くもの・`=>` が後ろに続くもの・メソッドの本体が後ろに続く
    ものを引数の並びとみなす。関数の型（`(a, b) => void`）の引数も数える。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `param-count` の `<パス>:<行番号>`
    """
    found: list[str] = []
    for path in sorted(p for p in sources if is_source(p)):
        code = strip_comments_and_strings(sources[path])
        for opened, closed in sorted(paired_parentheses(code)):
            head = code[max(0, opened - 120) : opened]
            tail = code[closed + 1 : closed + 200]
            method = METHOD_HEAD.search(head)
            is_method = bool(method) and method.group(1) not in CONTROL_KEYWORDS and bool(BODY_TAIL.match(tail))
            is_signature = bool(FUNCTION_HEAD.search(head)) or bool(ARROW_TAIL.match(tail)) or is_method
            if is_signature and count_parameters(code[opened + 1 : closed]) > MAX_PARAMETERS:
                found.append(f"{path}:{code.count(chr(10), 0, opened) + 1}")
    return {"param-count": found}


def scan_documentation(sources: dict[str, str]) -> dict[str, list[str]]:
    """doc コメントの無い公開の宣言を集める。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `undocumented-export` の `<パス>:<行番号>`。直前の空でない行が `*/` で終わって
        いない `export` の宣言を数える
    """
    found: list[str] = []
    for path in sorted(p for p in sources if is_source(p)):
        previous = ""
        for number, line in enumerate(sources[path].split("\n"), 1):
            if EXPORTED_DECLARATION.match(line) and not previous.rstrip().endswith("*/"):
                found.append(f"{path}:{number}")
            previous = line if line.strip() else previous
    return {"undocumented-export": found}


def scan_layout(sources: dict[str, str]) -> dict[str, list[str]]:
    """ファイルの置き場所と名前から読める違反を集める。

    `module-extra-implementation` は `domains/` と `src/services/` の下だけを見る。feature の
    `components/` のように、規約がモジュールフォルダかどうかを明示していない場所は数えない。

    @param sources パスから、コメントを落としていないソース全文への対応
    @returns `domain-type-only-file` / `module-extra-implementation` / `utils-form` /
        `folder-name` / `long-tsx` / `generic-name` / `test-file-name` / `test-location` ごとの説明
    """
    found: dict[str, list[str]] = {
        "domain-type-only-file": [],
        "module-extra-implementation": [],
        "utils-form": [],
        "folder-name": [],
        "long-tsx": [],
        "generic-name": [],
        "test-file-name": [],
        "test-location": [],
    }
    implementations: dict[str, list[str]] = {}
    misnamed_folders: set[str] = set()
    for path in sorted(sources):
        folder, name = os.path.split(path)
        misnamed_folders |= misnamed_module_folders(path)
        if is_source(path):
            found["utils-form"] += [path] if breaks_utils_form(path) else []
            found["generic-name"] += [path] if GENERIC_FILE_NAME.search(name.split(".")[0]) else []
            code_lines = [line for line in strip_comments_and_strings(sources[path]).split("\n") if line.strip()]
            is_long_tsx = name.endswith(".tsx") and len(code_lines) > MAX_TSX_LINES
            found["long-tsx"] += [f"{path}: {len(code_lines)} 行"] if is_long_tsx else []
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
    found["folder-name"] = sorted(misnamed_folders)
    return found


def breaks_utils_form(path: str) -> bool:
    """`src/utils/` の「フラットな 1 ファイル + PascalCase」から外れているかを判定する。

    @param path 実装ファイルのパス
    @returns `utils` 層のファイルで、フォルダの中に置かれているか、名前が PascalCase でなければ真
    """
    if layer_of(path) != "utils":
        return False
    below_layer = path.split("/utils/", 1)[1].split("/")
    in_shared_test_folder = len(below_layer) == 2 and below_layer[0] in TEST_FOLDERS
    is_flat = len(below_layer) == 1 or in_shared_test_folder
    return not is_flat or not (in_shared_test_folder or PASCAL_CASE_FILE.match(below_layer[-1]))


def misnamed_module_folders(path: str) -> set[str]:
    """ケバブケースでないモジュールフォルダを集める。

    @param path ファイルのパス
    @returns そのファイルを含むフォルダのうち、`domains` / `services` / `features` の層の下に
        あってケバブケースでないもの。テストの共有物のフォルダは除く
    """
    if layer_of(path) not in MODULE_LAYERS:
        return set()
    parts = path.split("/")[:-1]
    layer_depth = 2 if parts[0] == "src" else 1
    return {
        "/".join(parts[: depth + 1])
        for depth in range(layer_depth, len(parts))
        if parts[depth] not in TEST_FOLDERS and not KEBAB_CASE.match(parts[depth])
    }


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
    by_line = scan_lines(sources)
    by_layout = scan_layout(sources)
    found = {
        **scan_imports(sources),
        **by_line,
        **by_layout,
        **scan_signatures(sources),
        **scan_documentation(sources),
        "generic-name": [*by_layout["generic-name"], *by_line["generic-name"]],
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
