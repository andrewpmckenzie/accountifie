import json

from django.http import HttpResponse
from django.core.serializers.json import DjangoJSONEncoder

import accountifie.environment.api

def api(request, api_view):
    params = request.GET
    return HttpResponse(json.dumps(accountifie.environment.api.get(api_view, params), cls=DjangoJSONEncoder), content_type="application/json")
