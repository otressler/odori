from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404

from .models import HouseholdMembership


def household_for(user):
    membership = HouseholdMembership.objects.select_related("household").filter(user=user).first()
    if not membership:
        raise Http404("No household is available for this account.")
    return membership.household


def scoped(queryset, user):
    return queryset.filter(household=household_for(user))


def owner_household_for(user):
    membership = (
        HouseholdMembership.objects.select_related("household")
        .filter(user=user, role=HouseholdMembership.Role.OWNER)
        .first()
    )
    if not membership:
        raise PermissionDenied("Owner access is required.")
    return membership.household


def household_owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        owner_household_for(request.user)
        return view(request, *args, **kwargs)

    return wrapped
