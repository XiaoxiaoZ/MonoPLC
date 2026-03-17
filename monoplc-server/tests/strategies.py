"""
Hypothesis strategies for generating random Effect data.
Importable by test modules directly (unlike conftest.py).
"""

from hypothesis import strategies as st

from models import Effect, EffectType


# All valid EffectType values
effect_types = st.sampled_from(list(EffectType))

# Short target strings (matching PLC STRING[32])
targets = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz/_",
    min_size=0,
    max_size=16,
)

# Short payload strings (matching PLC STRING[64])
payloads = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-",
    min_size=0,
    max_size=32,
)

# Finite float values for Value field
values = st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False)


@st.composite
def effect_strategy(draw):
    """Generate a random Effect."""
    return Effect(
        e_type=draw(effect_types),
        target=draw(targets),
        payload=draw(payloads),
        value=draw(values),
    )


@st.composite
def effect_list_strategy(draw, min_size=0, max_size=20):
    """Generate a random list of Effects (M_PLC carrier)."""
    return draw(st.lists(effect_strategy(), min_size=min_size, max_size=max_size))
