from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

urlpatterns = [
	path("", login_required(views.inventory_page), name="inventory-page"),
	path("add/", login_required(views.inventory_create_page), name="inventory-create"),
	path(
		"<uuid:ingredient_id>/status/",
		login_required(views.inventory_status_page),
		name="inventory-status",
	),
]
