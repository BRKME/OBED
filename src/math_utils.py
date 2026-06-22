import math

MIN_TICK = -887272
MAX_TICK = 887272


def price_raw_from_sqrt_price_x96(sqrt_price_x96: int) -> float:
    """Цена token1 за token0 в "сырых" единицах токенов (без учёта decimals)."""
    sqrt_price = sqrt_price_x96 / (2 ** 96)
    return sqrt_price ** 2


def tick_from_price_raw(price_raw: float) -> int:
    if price_raw <= 0:
        raise ValueError("price_raw должен быть положительным")
    tick = math.floor(math.log(price_raw) / math.log(1.0001))
    return max(MIN_TICK, min(MAX_TICK, tick))


def human_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    """Цена token1 за token0 в человеческих единицах (с учётом decimals каждого токена)."""
    price_raw = price_raw_from_sqrt_price_x96(sqrt_price_x96)
    return price_raw * (10 ** (decimals0 - decimals1))


def round_tick_down(tick: int, spacing: int) -> int:
    t = (tick // spacing) * spacing
    return max(MIN_TICK, t)


def round_tick_up(tick: int, spacing: int) -> int:
    t = math.ceil(tick / spacing) * spacing
    return min(MAX_TICK, t)


def symmetric_range_ticks(current_tick: int, sqrt_price_x96: int, range_width_pct: float,
                           tick_spacing: int) -> tuple:
    """
    Считает [tickLower, tickUpper] симметрично вокруг текущей цены с шириной
    ±range_width_pct (в долях, например 0.10 = ±10%).
    Округляет наружу до tick_spacing, чтобы диапазон гарантированно покрывал
    запрошенную ширину.
    """
    price_raw = price_raw_from_sqrt_price_x96(sqrt_price_x96)
    price_lower = price_raw * (1 - range_width_pct)
    price_upper = price_raw * (1 + range_width_pct)

    tick_lower_raw = tick_from_price_raw(price_lower)
    tick_upper_raw = tick_from_price_raw(price_upper)

    tick_lower = round_tick_down(tick_lower_raw, tick_spacing)
    tick_upper = round_tick_up(tick_upper_raw, tick_spacing)

    if tick_lower >= tick_upper:
        # защита от вырожденного диапазона при очень маленьком range_width_pct
        tick_upper = tick_lower + tick_spacing

    return tick_lower, tick_upper


def fees_value_in_payout(tokens_owed0: int, tokens_owed1: int, decimals0: int, decimals1: int,
                          price_human_t1_per_t0: float, payout_is_token0: bool) -> float:
    """Оценка стоимости накопленных комиссий в payout-токене по текущей цене пула."""
    amt0 = tokens_owed0 / (10 ** decimals0)
    amt1 = tokens_owed1 / (10 ** decimals1)
    if payout_is_token0:
        return amt0 + (amt1 / price_human_t1_per_t0 if price_human_t1_per_t0 else 0)
    return amt1 + (amt0 * price_human_t1_per_t0)
