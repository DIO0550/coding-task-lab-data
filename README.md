# 隠しフェーズ仕様(Phase 2〜4)

**取り扱い注意: これらのファイルは被験エージェントに公開してはならない。** プライベートリポジトリで管理し、各 Phase 開始時に対応する Issue 本文として公開する。

各 Phase は Phase 1 完了後の同一リポジトリに対して、追加の変更要求として与える。

## 測定の狙い

| Phase | 変更内容 | 測定する設計力 | ファイル |
|---|---|---|---|
| 2 | 三人麻雀モード追加 | 既存ドメインの拡張性 | [phase2-sanma.md](./phase2-sanma.md) |
| 3 | 架空のローカル役追加 | 汚染耐性 + 役判定の拡張性(OCP) | [phase3-local-yaku.md](./phase3-local-yaku.md) |
| 4 | 点数計算エンジンの完全分離 | 層分離が本物か | [phase4-extraction.md](./phase4-extraction.md) |

## 共通の観測指標(被験者には伝えない)

- 変更 diff の行数 / 変更ファイル数
- Phase 1 で書かれた既存テストの破壊数(振る舞いテストなら壊れにくいはず)
- 規約(architecture / coding / hooks / testing / components)の遵守が変更後も維持されているか
- 既存の入出力JSON契約が後方互換を保っているか

### 規約の遵守を数える

`convention_score.py` が、採点対象のブランチをチェックアウトしたフォルダを読んで、規約からの外れを種別ごとに数える。
数える種別と、違反・参考値の分け方はスクリプト冒頭の説明が持つ。
数えるのは coding-task-lab の版 0.5.0 の規約で、それより前の版の成果物を読ませると、当時は無かった規約でも数える。

```bash
python3 convention_score.py <coding-task-lab のチェックアウト>/mahjong          # 人が読む形
python3 convention_score.py <coding-task-lab のチェックアウト>/mahjong --json   # Phase ごとに保存して比べる形
python3 convention_score_cases.py                                              # スクリプトを直したときの判定表
```

- 外部パッケージは要らない（`generate_expected.py` と違い、Python 3.10 以上の標準ライブラリだけで動く）
- lint / typecheck / test / build の成否と、機能の正しさは見ない。別に走らせる
- 設計の良し悪し（ロジックの帰属先・命名・テストが仕様を捉えているか）は数えられない。そこはレビューで見る

## 出題順と運用

1. Phase 1 を完了・採点・スナップショット保存
2. 同一リポジトリ状態から Phase 2 を出題(Issue 起票)→ 採点・保存
3. Phase 2 完了状態から Phase 3 → 採点・保存
4. Phase 3 完了状態から Phase 4 → 採点・保存

各 Phase の開始時まで対応する Issue を作成しない(先読み防止)。Phase をまたぐ際は clean な作業ツリーから開始する。
