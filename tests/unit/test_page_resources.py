"""The page may fetch Mod pictures from Nexus image hosts and nothing else remote."""
import pytest

from backend.app.composition import page_resource_allowed


def test_local_document_resources_are_allowed():
    assert page_resource_allowed('about:blank', False, 'GET')
    assert page_resource_allowed('data:image/png;base64,AAAA', True, 'GET')


def test_nexus_pictures_are_allowed_as_images_only():
    url = 'https://staticdelivery.nexusmods.com/mods/9791/images/385/a.png'
    assert page_resource_allowed(url, True, 'GET')
    assert page_resource_allowed('https://images.nexusmods.com/mod-images/9791/t/large/a.png', True, 'GET')
    assert not page_resource_allowed(url, False, 'GET')  # not as script, fetch or document
    assert not page_resource_allowed(url, True, 'POST')


@pytest.mark.parametrize('url', [
    'http://staticdelivery.nexusmods.com/a.png', 'https://api.nexusmods.com/v2/graphql',
    'https://staticdelivery.nexusmods.com.evil.test/a.png', 'https://user:pw@images.nexusmods.com/a.png',
    'https://images.nexusmods.com:8443/a.png', 'https://i.imgur.com/a.png', 'file:///C:/Windows/win.ini',
    'https://127.0.0.1/a.png', 'https://images.nexusmods.com:bad/a.png',
])
def test_everything_else_stays_blocked(url):
    assert not page_resource_allowed(url, True, 'GET')
