import os, csv, json
from ast import literal_eval
from StringIO import StringIO
import datetime
import gzip
import csv
import datetime
from dateutil.parser import parse

import logging
import pandas as pd

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.management import call_command
from django.core.serializers.json import DjangoJSONEncoder
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponse, Http404
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.views.generic.detail import DetailView
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from forms import FileForm

from accountifie.middleware.docengine import getCurrentRequest
from accountifie.tasks.utils import task, utcnow
from accountifie.tasks.models import DeferredTask, isDetachedTask, setProgress, setStatus


from accountifie.gl.models import ExternalBalance, Transaction, TranLine, Company

from accountifie.query.query_manager import QueryManager
from accountifie.forecasts.models import Forecast


from .forms import SplashForm
import accountifie._utils

logger = logging.getLogger('default')


def company_context(request):
    """Context processor referenced in settings.
    This puts the current company ID into any request context, 
    thus allowing templates to refer to it

    This is not a view.
    """
    
    company_id = accountifie._utils.get_company(request)
    data = {'company_id': company_id}
    if company_id:
        try:
            company = Company.objects.get(pk=company_id)
            data.update({'company':company})
        except Company.DoesNotExist:
            pass 
    return data



@login_required
def choose_company(request, company_id):
    "Hit this to switch companies"

    company_list = [x.id for x in Company.objects.all()]
    
    if company_id not in company_list:
        raise ValueError('%s is not a valid company' % company_id)
    request.session['company_id'] = company_id
    dest =  request.META.get('HTTP_REFERER', '/')
    return HttpResponseRedirect(dest)


@task
def run_recalc(*args, **kwargs):
    success_url = kwargs.get('success_url', None)
    call_command('recalculate')
    return 0

@user_passes_test(lambda u: u.is_superuser)
def recalculate(request):
    out = StringIO()
    result, out, err = run_recalc(task_title="Recalculating GL",task_success_url='/dashboard')

    if result != 0:
        return HttpResponseRedirect('/dashboard')
    else:
        return HttpResponseRedirect(reverse('task_result', kwargs={'pid':out,}))


@task
def run_logclean(*args, **kwargs):
    call_command("clean_log",clean_to=kwargs.pop('clean_to'))

@user_passes_test(lambda u: u.is_superuser)
def cleanlogs(request):
    out = StringIO()
    clean_to = request.GET['clean_to']
    result, out, err = run_logclean(task_title="Cleaning Logs",stdout=out, clean_to=clean_to)
    return HttpResponseRedirect('/')



@user_passes_test(lambda u: u.is_superuser)
def download_data(request):
    
    format = request.GET.get('format', 'sql')
    assert format in ['sql', 'json'], "Can only download data in json or sql format"
    if format == 'sql':
        filename = 'latest.sql.gz'
        dbparams = settings.DATABASES['default']
        cmd = "mysqldump --user=%(USER)s --password=%(PASSWORD)s %(NAME)s | gzip" % dbparams
        data = os.popen(cmd).read()
    else:
        buf = StringIO()
        filename = 'latest.json.gz'
        call_command("dumpdata", indent=2, stdout=buf)
        data = buf.getvalue()

        #pass 2 - gzip it
        buf = StringIO()
        #tricky incantation to make gzipfiles in memory
        with gzip.GzipFile(filename=filename, mode='wb', fileobj=buf) as gzip_obj:
            gzip_obj.write(data)

        data = buf.getvalue()


    resp = HttpResponse(data, content_type="application/octet-stream")
    resp['Content-Disposition'] = 'attachment; filename="%s"' % filename
    resp['Content-Encoding'] = 'gzip'
    return resp