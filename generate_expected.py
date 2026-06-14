#!/usr/bin/env python3
"""
mahjong ライブラリで麻雀点数の期待値データを生成する。

2つのモードを持つ:
  1. CURATED  : 観点を狙った確定ケース(役・符境界・役満・複合・異常系)
  2. RANDOM   : 山から配牌して和了形をランダムに生成(網羅の補完用)

spec-phase1.md の確定ルールに OptionalRules を合わせている。
出力は JSON 配列。各要素が1テストケースの期待値。

実行: python3 generate_expected_v2.py [--random N] [--seed S]
"""
import json
import random
import argparse
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.tile import TilesConverter
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.constants import EAST, SOUTH, WEST, NORTH
from mahjong.shanten import Shanten

calc = HandCalculator()
shanten_calc = Shanten()
WIND = {"ton": EAST, "nan": SOUTH, "sha": WEST, "pei": NORTH}


def spec_rules():
    """spec-phase1.md 準拠のルール設定"""
    return OptionalRules(
        has_open_tanyao=True,      # 喰いタンあり
        has_aka_dora=False,        # 赤は牌姿で明示する場合のみ(誤検出回避でデフォルトoff)
        has_double_yakuman=True,   # ダブル役満まで(複合役満は要spec補正)
        kiriage=False,             # 切り上げ満貫なし
        fu_for_open_pinfu=True,    # 喰い平和形30符
        fu_for_pinfu_tsumo=False,  # 平和ツモ20符
        renhou_as_yakuman=False,   # 人和なし
    )


def t(man="", pin="", sou="", honors=""):
    return TilesConverter.string_to_136_array(man=man, pin=pin, sou=sou, honors=honors)


def run(tiles, win_tile, melds=None, dora_indicators=None, **cfg):
    config = HandConfig(
        player_wind=WIND.get(cfg.pop("player_wind", "nan")),
        round_wind=WIND.get(cfg.pop("round_wind", "ton")),
        options=spec_rules(),
        **cfg,
    )
    return calc.estimate_hand_value(
        tiles, win_tile,
        melds=melds, dora_indicators=dora_indicators, config=config,
    )


def to_record(case_id, note, result):
    if result.error:
        return {"id": case_id, "note": note, "valid": False, "error": result.error}
    return {
        "id": case_id, "note": note, "valid": True,
        "han": result.han, "fu": result.fu,
        "yaku": [str(y) for y in result.yaku],
        "fu_details": result.fu_details,
        "cost": result.cost,
    }


# ============================================================
# 1. CURATED 確定ケース(牌姿検証済み)
# ============================================================
def curated():
    out = []
    # A01 リーチのみ(么九含みで役を作らない)
    out.append(to_record("A01", "リーチのみ 1翻40符",
        run(t(man="123789", pin="123789", sou="55"), t(sou="5")[0], is_riichi=True)))
    # A04 平和ツモ(タンヤオ複合) 20符
    out.append(to_record("A04", "平和ツモ(タンヤオ複合) 20符",
        run(t(man="234567", pin="234567", sou="55"), t(man="7")[0], is_tsumo=True)))
    # A05 平和ロン 30符
    out.append(to_record("A05", "平和ロン 30符",
        run(t(man="234567", pin="234567", sou="55"), t(man="7")[0])))
    # A06 一盃口のみ
    out.append(to_record("A06", "一盃口",
        run(t(man="112233", pin="456", sou="789", honors="11"), t(honors="1")[0])))
    # A08 一気通貫(門前)
    out.append(to_record("A08", "一気通貫+平和",
        run(t(man="123456789", pin="99", sou="678"), t(man="9")[0])))
    # D01 七対子 25符
    out.append(to_record("D01", "七対子 25符2翻",
        run(t(man="1133", pin="5577", sou="99", honors="1122"), t(honors="2")[0])))
    # D02 七対子ツモ
    out.append(to_record("D02", "七対子ツモ",
        run(t(man="2244", pin="6688", sou="33", honors="2255"), t(sou="3")[0], is_tsumo=True)))
    # H03 四暗刻単騎(ダブル役満)
    out.append(to_record("H03", "四暗刻単騎(ダブル役満)",
        run(t(man="111", pin="333", sou="555777", honors="11"), t(honors="1")[0], is_tsumo=True)))
    # H06 字一色(複合役満 → spec補正対象)
    out.append(to_record("H06", "字一色系複合(spec補正要)",
        run(t(honors="11122233344455"), t(honors="5")[0])))
    # I01 リーチ平和三色ツモ(満貫)
    out.append(to_record("I01", "リーチ平和三色ツモ 満貫",
        run(t(man="234567", pin="234", sou="23499"), t(sou="4")[0], is_riichi=True, is_tsumo=True)))
    # J04 海底摸月+タンヤオ
    out.append(to_record("J04", "海底摸月+平和+タンヤオ",
        run(t(man="234567", pin="234567", sou="55"), t(man="7")[0], is_tsumo=True, is_haitei=True)))
    # J08 ダブルリーチ
    out.append(to_record("J08", "ダブルリーチ+平和+タンヤオ",
        run(t(man="234567", pin="234567", sou="55"), t(man="7")[0], is_daburu_riichi=True)))
    # K03 役なし(ダマロン)
    out.append(to_record("K03", "役なし(no_yaku想定)",
        run(t(man="123789", pin="123789", sou="55"), t(sou="5")[0])))
    return out


# ============================================================
# 2. RANDOM 生成(山から14枚配って和了形のみ採用)
# ============================================================
SUITS = ["m", "p", "s"]


def _build_winning_hand(rng):
    """4面子+1雀頭を構築的に作り、136配列を返す。失敗時None"""
    counts = {"m": [0] * 9, "p": [0] * 9, "s": [0] * 9, "z": [0] * 7}

    def can_add(suit, idx, k):
        return counts[suit][idx] + k <= 4

    melds = 0
    tries = 0
    while melds < 4 and tries < 100:
        tries += 1
        if rng.random() < 0.6:  # 順子
            suit = rng.choice(SUITS)
            start = rng.randint(0, 6)
            if all(can_add(suit, start + o, 1) for o in range(3)):
                for o in range(3):
                    counts[suit][start + o] += 1
                melds += 1
        else:  # 刻子
            suit = rng.choice(SUITS + ["z"])
            mx = 7 if suit == "z" else 9
            idx = rng.randint(0, mx - 1)
            if can_add(suit, idx, 3):
                counts[suit][idx] += 3
                melds += 1
    if melds < 4:
        return None
    tries = 0
    while tries < 100:
        tries += 1
        suit = rng.choice(SUITS + ["z"])
        mx = 7 if suit == "z" else 9
        idx = rng.randint(0, mx - 1)
        if counts[suit][idx] + 2 <= 4:
            counts[suit][idx] += 2
            break
    else:
        return None

    def s(suit):
        return "".join(str(i + 1) * counts[suit][i] for i in range(len(counts[suit])))

    return t(man=s("m"), pin=s("p"), sou=s("s"), honors=s("z"))


def random_cases(n, seed):
    rng = random.Random(seed)
    out = []
    made = 0
    attempts = 0
    while made < n and attempts < n * 500:
        attempts += 1
        hand = _build_winning_hand(rng)
        if not hand or len(hand) != 14:
            continue
        if shanten_calc.calculate_shanten(TilesConverter.to_34_array(hand)) != -1:
            continue
        win_tile = rng.choice(hand)
        is_tsumo = rng.random() < 0.5
        try:
            result = run(hand, win_tile, is_tsumo=is_tsumo,
                         player_wind=rng.choice(["nan", "sha", "pei"]),
                         round_wind="ton")
        except Exception:
            continue
        if result.error:
            continue  # 役なしは除外(役あり和了のみ採用)
        made += 1
        rec = to_record(f"R{made:03d}", f"random(tsumo={is_tsumo})", result)
        rec["input_readable"] = TilesConverter.to_one_line_string(hand)
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=10, help="ランダム生成件数")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    data = {
        "rules": "spec-phase1: kuitan=on, kiriage=off, pinfu-tsumo=20fu, double-yakuman=on(複合役満は要補正)",
        "curated": curated(),
        "random": random_cases(args.random, args.seed),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
