import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)


# Create your views here.
def index(request):
    return HttpResponse("Welcome to the Pennini Cup!")
