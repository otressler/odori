from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404

from .models import Household, HouseholdMembership


def is_global_admin(user):
    return bool(user.is_authenticated and user.is_superuser)


def household_for(user):
    memberships = HouseholdMembership.objects.select_related("household").filter(user=user)
    active_household_id = getattr(user, "_active_household_id", None)
    if is_global_admin(user) and active_household_id:
        household = Household.objects.filter(id=active_household_id).first()
        if household:
            return household
    membership = memberships.filter(household_id=active_household_id).first()
    if not membership:
        membership = memberships.first()
    if not membership:
        if is_global_admin(user):
            household = Household.objects.order_by("created_at").first()
            if household:
                return household
        raise Http404("No household is available for this account.")
    return membership.household


def scoped(queryset, user):
    return queryset.filter(household=household_for(user))


def owner_household_for(user):
    household = household_for(user)
    if is_global_admin(user):
        return household
    membership = HouseholdMembership.objects.filter(
        user=user, household=household, role=HouseholdMembership.Role.OWNER
    ).first()
    if not membership:
        raise PermissionDenied("Owner access is required.")
    return membership.household


def household_admin_for(user):
    household = household_for(user)
    if is_global_admin(user):
        return household
    membership = HouseholdMembership.objects.filter(
        user=user,
        household=household,
        role__in=[HouseholdMembership.Role.OWNER, HouseholdMembership.Role.ADMIN],
    ).first()
    if not membership:
        raise PermissionDenied("Household admin access is required.")
    return membership.household


def household_owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        owner_household_for(request.user)
        return view(request, *args, **kwargs)

    return wrapped


def household_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        household_admin_for(request.user)
        return view(request, *args, **kwargs)

    return wrapped
