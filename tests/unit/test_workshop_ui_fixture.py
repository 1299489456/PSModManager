"""Keep simulated account receipts representative of new subscriptions."""
from tests.desktop.harness.workshop_fixture import FixtureSteamSDK


def test_new_public_card_can_be_subscribed_before_local_install():
    sdk = FixtureSteamSDK(['3828868413'])
    identity = '3828921912'
    call = sdk.begin('subscribe', identity)
    action, key, serial, started = sdk.pending[0]
    sdk.pending[0] = (action, key, serial, started - 2)
    assert sdk.callbacks() == [dict(kind='call', call=call, callback=1313,
        workshop_id=identity, result=1, io_failure=False)]
    assert sdk.item(identity)['subscribed'] is True
    assert sdk.item(identity)['steam_installed'] is False
