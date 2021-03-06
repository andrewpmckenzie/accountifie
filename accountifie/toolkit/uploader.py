"""
Adapted with permission from ReportLab's DocEngine framework
"""

import os
import json
from StringIO import StringIO
import datetime
from ast import literal_eval

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.generic.detail import DetailView
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.db import transaction
from django.db.models.loading import cache
from django.core.serializers.json import DjangoJSONEncoder

from accountifie.tasks.utils import task, utcnow
from accountifie._utils import get_default_company, csv_to_modelattr, files_for_dir, create_instance, dirty_key


from forms import FileForm
import accountifie._utils

import logging

logger = logging.getLogger('default')


DATA_ROOT = getattr(settings, 'DATA_DIR', os.path.join(settings.ENVIRON_DIR, 'data'))
INCOMING_ROOT = os.path.join(DATA_ROOT, 'incoming')
PROCESSED_ROOT = os.path.join(DATA_ROOT, 'processed')


@login_required
def upload_file(request, **config):
    file_type = config['file_type']

    context = {'file_type': file_type,}     
    form = FileForm(request.POST, request.FILES)

    if form.is_valid():
        upload = request.FILES.values()[0]
        file_name = upload._name
        file_name_with_timestamp = save_file(upload)
        company = accountifie._utils.get_company(request)

        model = config.pop('model')
        unique = config.pop('unique')
        name_cleaner = config.pop('name_cleaner')
        value_cleaner = config.pop('value_cleaner')
        exclude = config.pop('exclude')
        post_process = config.pop('post_process')

        #result is zero if it was able to start the background task successfully.
        result, out, err = process_incoming_file(model, unique, name_cleaner, value_cleaner, exclude, post_process,
                                                 file_type=config['file_type'],
                                                 file_name=file_name_with_timestamp,
                                                 company=company,
                                                 task_title="%s data for %s" % (file_type, company),
                                                 task_success_url=reverse('upload_complete'),
                                                 )

        if result != 0:
            context.update({'file_name': file_name, 'success': False, 'out': out, 'err': err})
            messages.error(request, 'Could not process the file provided, please see below')
            return render_to_response('uploaded.html', context, context_instance=RequestContext(request))
        else:
            return HttpResponseRedirect(reverse('task_result', kwargs={'pid':out,}))
    else:
        context.update({'file_name': file_name, 'success': False, 'out': out, 'err': err})
        messages.error(request, 'Could not process the file provided, please see below')
        return render_to_response('uploaded.html', context, context_instance=RequestContext(request))


def save_file(f):
    new_name = '%s_%s' % (utcnow().strftime('%Y%m%d%H%M%S'), f._name)
    if not os.path.exists(INCOMING_ROOT):
        os.makedirs(INCOMING_ROOT)
    full_path = os.path.join(INCOMING_ROOT, new_name)
    with open(full_path, 'wb') as destination:
        for chunk in f.chunks():
            destination.write(chunk)

    return new_name 


@task
def process_incoming_file(model, unique, name_cleaner, value_cleaner, exclude, post_process, **config):
    out = StringIO()
    file_name = config.pop('file_name')
    context = {'file_name': file_name}
    file_type = config.pop('file_type')
    success_url = config.get('success_url', None)

    incoming_name = os.path.join(INCOMING_ROOT, file_name)
    
    with open(incoming_name, 'U') as f:
        data = json.loads(load_file(file=f, stdout=out, model=model, unique=unique, name_cleaner=name_cleaner,
                                    value_cleaner=value_cleaner, exclude=exclude, post_process=post_process, **config))

    context['found'] = sum([len(data[k]) for k in data])
    context['data'] = data['saved']
    if data['saved']:
        context['example'] = data['saved'][0]
    elif data['key_errors']:
        context['example'] = data['key_errors']
    else:
        context['example'] = dict(no_keys_found='no values found')
    for k in [x for x in data.keys() if x not in context.keys()]:
        context[k] = data[k]
    
    #post_processing(file_type)
    
    # move the file into 'processed' directory
    if not os.path.exists(PROCESSED_ROOT):
        os.makedirs(PROCESSED_ROOT)
    processed_name = os.path.join(PROCESSED_ROOT, file_name)
    os.rename(incoming_name, processed_name)
    print json.dumps(context, indent=4, cls=DjangoJSONEncoder)
    return 0


def post_processing(file_type):
    if file_type == 'expense':
        match_expenses_to_vendors(base.Expense.objects.filter(counterparty=None))
    elif file_type == 'mcard':
        match_mastercard_to_vendors(base.Mcard.objects.filter(counterparty=None))
    return


def load_file(**config):
    file = config.pop('file', None)
    company = config.get('company', get_default_company())
    model = config.get('model')
    unique = config.get('unique')
    name_cleaner = config.get('name_cleaner')
    value_cleaner = config.get('value_cleaner')
    exclude = config.get('exclude', [])
    data = csv_to_modelattr(file, name_cleaner=clean_csv_fields, company=company)
    with transaction.atomic():
        results = save_data(data, company, model, unique, name_cleaner, value_cleaner, exclude)
        
    return json.dumps(results, ensure_ascii=False)
    
                
def save_data(data, company, model, unique, name_cleaner, value_cleaner, exclude):
    if name_cleaner == None:
        name_cleaner = lambda name: name
    if value_cleaner == None:
        value_cleaner = lambda name, value: value
    if unique == None:
        unique = lambda instance: instance
    model = cache.get_model(*model.split('.'))

    dups = []
    saved = []
    value_errors = []

    for index, row in enumerate(data):
        dirty = dirty_key(row, model, name_cleaner=name_cleaner, value_cleaner=value_cleaner)
        
        if dirty:
            #if a key is wrong the whole file will fail anyway
            return dict(dups=dups, saved=saved, key_errors=dirty, value_errors=value_errors)
        
        unique_instance = create_instance(row, model, name_cleaner=name_cleaner, value_cleaner=value_cleaner,
                                              unique=unique, company=company, exclude=exclude)
        if unique_instance:
            try:
                unique_instance.save()
            except Exception as e:
                message = getattr(e, 'messages', [row])[0]
                value_errors.append((e.__class__.__name__, index, message))
                continue
            #need to deconstruct the model instance for later json serialization
            saved.append(dict([(k, v)for k,v in unique_instance.__dict__.items() if k in [f.name for f in unique_instance._meta.fields]]))
        else:
            decoded = dict((k.decode('utf-8', 'ignore'),v.decode('utf-8', 'ignore')) for k,v in row.iteritems())
            dups.append(decoded)
    
    return dict(dups=dups, saved=saved, key_errors=[], value_errors=value_errors)


def clean_csv_fields(name):
    return name.lower().replace(' ', '_').replace('-', '_').replace('check_or_slip_#', 'check_or_slip')
    