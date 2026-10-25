"""通知模板保留来源事实，图片是受数量限制的消息组件。"""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def template_module(monkeypatch):
    path = Path(__file__).parents[2] / 'integrations/astrbot_plugin_anisignal/notification_template.py'
    spec = importlib.util.spec_from_file_location('notification_template_test', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def notice():
    return {'umo':'qq:GroupMessage:g','body':'旧版完整正文','kind':'daily','max_images':2,
            'images':['https://example.test/1.jpg','https://example.test/2.jpg'],
            'brief':{'mode':'daily','items':[
                {'title':'第一条标题','summary':'编辑摘要','image':'https://example.test/1.jpg',
                 'sources':[{'text':'真实原文 {images}','source':'官方甲','url':'https://example.test/a',
                             'published_at':'2026-10-25T04:30:00Z','image':'https://example.test/1.jpg'}]},
                {'title':'第二条标题','summary':'第二条摘要','image':'https://example.test/2.jpg',
                 'sources':[{'text':'第二条原文','source':'官方乙','url':'https://example.test/b',
                             'published_at':'2026-10-24T23:00:00','image':'https://example.test/2.jpg'}]},
            ]}}


def test_fields_use_original_snapshot_and_beijing_publication_time(template_module):
    row=notice();row['brief']['items']=row['brief']['items'][:1]
    before=copy.deepcopy(row)
    result=template_module.render_notification(row,{'notification_template':'{title}\n{content}\n{summary}\n{time}\n{source}\n{url}'})
    assert result.text=='第一条标题\n真实原文 {images}\n编辑摘要\n2026-10-25 12:30\n官方甲\nhttps://example.test/a'
    assert all(isinstance(part,str) for part in result.parts)
    assert '旧版完整正文' not in result.text and row==before


def test_items_keep_each_sources_text_time_and_image_together(template_module):
    result=template_module.render_notification(notice(),{'notification_template':'{intro}\n{items}',
        'notification_item_template':'{index}. {title}\n{original}\n{time}\n{source}\n{url}\n{images}'},'引导语')
    assert result.customized
    assert [(type(p).__name__,p.url) for p in result.parts if not isinstance(p,str)]==[
        ('NotificationImage','https://example.test/1.jpg'),('NotificationImage','https://example.test/2.jpg')]
    first,second=[p for p in result.parts if isinstance(p,str)]
    assert '1. 第一条标题' in first and '2026-10-25 12:30' in first and '官方甲' in first
    assert '2. 第二条标题' in second and '2026-10-25 07:00' in second and '官方乙' in second
    assert '旧版完整正文' not in result.text


def test_repeated_image_placeholders_share_one_budget(template_module):
    row=notice();row['max_images']=1
    result=template_module.render_notification(row,{'notification_template':'{images}\n{items}\n{images}'})
    assert [p.url for p in result.parts if not isinstance(p,str)]==['https://example.test/1.jpg']


def test_missing_publication_and_original_are_not_replaced_with_summary(template_module):
    row=notice();row['brief']['items']=[{'title':'标题','summary':'只有摘要','sources':[{'text':''}]}]
    result=template_module.render_notification(row,{'notification_template':'标题：{title}\n原文：{content}\n时间：{time}'})
    assert result.text=='标题：标题\n原文：\n时间：'
    assert '只有摘要' not in result.text


def test_image_links_do_not_implicitly_attach_images(template_module):
    result=template_module.render_notification(notice(),{'notification_template':'{image_urls}'})
    assert result.text=='https://example.test/1.jpg\nhttps://example.test/2.jpg'
    assert all(isinstance(p,str) for p in result.parts)


def test_legacy_template_keeps_body_and_does_not_consume_original_braces(template_module):
    row=notice();row['body']='原文中的 {intro} 和 {images} 都是文字'
    result=template_module.render_notification(row,{},'引导语 {body}')
    assert not result.customized and result.text=='引导语 {body}\n\n'+row['body']
    assert template_module.render_notification(row,{'notification_template':'{intro}'},'引导语').text=='引导语\n\n'+row['body']


def test_image_only_template_without_images_still_has_a_fallback(template_module):
    row={'umo':'qq:GroupMessage:g','body':'定时提醒正文','images':[]}
    result=template_module.render_notification(row,{'notification_template':'{images}'})
    assert result.text=='定时提醒正文'
    result=template_module.render_notification(row,{'notification_template':'{content}'})
    assert result.text=='定时提醒正文'
