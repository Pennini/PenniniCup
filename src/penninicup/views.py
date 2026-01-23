import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)


# Create your views here.
def index(request):
    logger.info("Index view accessed.", extra={"view": "index"})
    return HttpResponse("Welcome to the Pennini Cup!")
