"""FreeDMR parity: LC rewrite only when destination TG changes."""
from bridge_helpers import target_requires_emb_lc_rewrite, target_requires_lc_rewrite


def test_same_tg_passthrough():
    tg = (116).to_bytes(3, 'big')
    assert target_requires_emb_lc_rewrite(tg, tg) is False
    assert target_requires_lc_rewrite(tg, tg) is False


def test_remap_requires_rewrite():
    assert target_requires_emb_lc_rewrite(
        (116).to_bytes(3, 'big'),
        (9).to_bytes(3, 'big'),
    ) is True
