from django.http import Http404

from .models import HouseholdMembership


def household_for(user):
    membership = HouseholdMembership.objects.select_related("household").filter(user=user).first()
    if not membership:
        raise Http404("No household is available for this account.")
    return membership.household


def scoped(queryset, user):
    return queryset.filter(household=household_for(user))
