"""0.2.2 actual WebView rendering; account and AI effects stay in owned fixtures."""
import json
import time


def check(window, context, report, wait, click, text_button, shot):
    def fill(selector, value):
        window.evaluate_js(f'''(()=>{{const e=document.querySelector({json.dumps(selector)});
            e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));
            e.dispatchEvent(new Event('change',{{bubbles:true}}))}})()''')

    click(window, '[data-testid=mods-available-all]')
    wait(window, 'document.querySelectorAll(".bed.avail .row.selected").length>0 || document.querySelectorAll(".bed.avail .row[aria-selected=true]").length>0')
    report['checks'].append('Mods select-all control selects the visible available rows')
    click(window, '[data-testid=mods-available-all]')
    click(window, '.row[data-id="CyberElectronics"]')
    wait(window, 'Array.from(document.querySelectorAll(".detail [data-testid=mod-cover] img")).some(i=>i.complete&&i.naturalWidth>0&&i.src.startsWith("data:image/"))')
    shot(window, 'mods-cached-cover-1366')
    report['checks'].append('Missing package cover is displayed as host-cached image bytes in actual WebView2')

    click(window, '[data-testid=workspace-settings]')
    wait(window, 'document.querySelector("[data-testid=workshop-auto-update]") && !document.querySelector("[data-testid=workshop-auto-update]").disabled')
    assert window.evaluate_js('document.querySelector("[data-testid=workshop-auto-update]").checked===false')
    assert context.preferences.read_config().get('workshop', {}).get('auto_update') is not True
    shot(window, 'update-policy-1366')
    click(window, '[data-testid=info-ai]')
    wait(window, 'document.querySelector("[data-testid=ai-provider-url]")?.value.startsWith("http://127.0.0.1:")')
    url = window.evaluate_js('document.querySelector("[data-testid=ai-provider-url]").value')
    text_button(window, '添加服务')
    wait(window, 'document.querySelector("[data-testid=ai-provider-name]").value===""')
    for field, value in [('name', '节省预算的翻译'), ('url', url), ('model', 'owned-budget-translation'), ('key', 'owned-local-fixture-key')]:
        fill('[data-testid=ai-provider-' + field + ']', value)
    if window.evaluate_js('document.querySelector(".provider-key-status input").checked'):
        click(window, '.provider-key-status input')
    assert window.evaluate_js('document.querySelector(".provider-key-status input").checked===false')
    click(window, '[data-testid=ai-provider-save]')
    wait(window, 'document.querySelectorAll(".provider-list button").length===2')
    provider_id = window.evaluate_js('Array.from(document.querySelector("[data-testid=ai-binding-translation]").options).find(o=>o.text.includes("节省预算的翻译")).value')
    fill('[data-testid=ai-binding-translation]', provider_id)
    wait(window, 'document.querySelector("[data-testid=ai-binding-translation]").value===' + json.dumps(provider_id))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and context.preferences.read_config()['ai_providers']['bindings']['translation'] != provider_id:
        time.sleep(.05)
    bindings = context.preferences.read_config()['ai_providers']['bindings']
    assert bindings == dict(analysis='default', translation=provider_id)
    assert not window.evaluate_js('document.querySelector("[data-testid=ai-provider-key]").value')
    shot(window, 'ai-providers-1366')
    window.resize(1100, 760)
    wait(window, 'innerWidth>=1060&&innerWidth<=1100')
    assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1')
    shot(window, 'ai-providers-1100')
    window.resize(1366, 860)
    report['checks'].append('Settings UI creates a second session-key Provider, binds translation only, and never echoes its key')

    click(window, '[data-testid=workshop-open]')
    text_button(window, '我的订阅')
    wait(window, 'document.querySelectorAll(".ws-card").length===7')
    fill('[data-testid=workshop-page-size]', '12')
    wait(window, 'document.querySelectorAll(".ws-card").length===7')
    shot(window, 'workshop-cards-1366')
    click(window, '[data-testid=card-unsubscribe-3828868413]')
    click(window, '[data-testid=workshop-unsubscribe-confirm]')
    wait(window, 'document.querySelector("[data-testid=card-subscribe-3828868413]") && !document.querySelector("[data-testid=card-subscribe-3828868413]").disabled')
    assert not window.evaluate_js('Boolean(document.querySelector(".ws-detail-hero"))')
    click(window, '[data-testid=card-subscribe-3828868413]')
    wait(window, 'document.querySelector("[data-testid=card-unsubscribe-3828868413]") && !document.querySelector("[data-testid=card-unsubscribe-3828868413]").disabled')
    report['checks'].append('Workshop card unsubscribe and subscribe confirm in place through isolated SDK callbacks')
    window.evaluate_js('Array.from(document.querySelectorAll(".ws-card")).find(b=>b.textContent.includes("Item Details")).querySelector(".ws-card-main").click()')
    wait(window, 'document.querySelector(".ws-detail-hero")')
    click(window, '[data-testid=workshop-translate]')
    wait(window, 'document.querySelector("[data-testid=workshop-translation-toggle]") && document.querySelector("[data-testid=workshop-panel] .desc")?.textContent.includes("界面验收译文")')
    request_count = len(context._ui_ai_fixture.requests)
    assert request_count > 0
    assert all(row['body']['model'] == 'owned-budget-translation' for row in context._ui_ai_fixture.requests)
    click(window, '[data-testid=workshop-translation-toggle]')
    click(window, '[data-testid=workshop-translation-toggle]')
    text_button(window, '返回列表')
    window.evaluate_js('Array.from(document.querySelectorAll(".ws-card")).find(b=>b.textContent.includes("Item Details")).querySelector(".ws-card-main").click()')
    wait(window, 'document.querySelector("[data-testid=workshop-translation-toggle]") && document.querySelector("[data-testid=workshop-panel] .desc")?.textContent.includes("界面验收译文")')
    assert len(context._ui_ai_fixture.requests) == request_count
    assert (context.paths.root/'ai-translations.json').is_file()
    report['checks'].append('Translation used its bound model; original/translation toggles and detail revisit reused completed text with zero extra requests')
    report['translation_request_count'] = request_count
    shot(window, 'translation-cached-1366')
    window.resize(1100, 760)
    wait(window, 'innerWidth>=1060&&innerWidth<=1100')
    assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1')
    shot(window, 'translation-cached-1100')
