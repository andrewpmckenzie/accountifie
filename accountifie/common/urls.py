"""
Adapted with permission from ReportLab's DocEngine framework
"""




from django.conf import settings
from django.conf.urls import patterns, include, url
from django.core.urlresolvers import reverse_lazy
from django.template.loader import select_template
from django.views.generic.base import RedirectView, TemplateView

import accountifie.common.views

if 'docengine.register' in settings.INSTALLED_APPS:
    if getattr(settings, 'REGISTER_ONE_STEP', False):
        _ACCOUNT_URLS = 'docengine.register.simple_urls'
    else:
        _ACCOUNT_URLS = 'docengine.register.urls'
else:
    _ACCOUNT_URLS = 'accountifie.common.auth_urls'




urlpatterns = patterns('',
    url(r'^test-7491/$', accountifie.common.views.test7491, name='health-check'),
    url(r'^tests/error/$', accountifie.common.views.deliberateError, name='deliberate error'),
    url(r'^api/upload/$', accountifie.common.views.upload),
    url(r'^dashboard/', include('accountifie.dashboard.urls'), name="docengine_dashboard"),
    url(r'^docs/$', TemplateView.as_view(template_name='common/docs.html')),
    url(r'^about/$', TemplateView.as_view(template_name='common/about.html')),
    url(r'', include(_ACCOUNT_URLS)),
    url(r'^$', accountifie.common.views.index, name='index'),
    url(r'^check_all_tasks/$', accountifie.common.views.check_all_tasks, name="check_all_tasks"),
    # shortcuts
    #url(r'^login/', RedirectView.as_view(url=reverse_lazy("accounts_login"))),
    #url(r'^logout/', RedirectView.as_view(url=reverse_lazy("accounts_logout"))),


)
if "accountifie.tasks" in settings.INSTALLED_APPS:
    urlpatterns += patterns('',
            url(r'^tasks/', include('accountifie.tasks.urls'), name="docengine_tasks"),
            )
