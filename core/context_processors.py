from .models import HouseholdMembership


def owner_navigation(request):
    memberships = []
    current_household = None
    if request.user.is_authenticated:
        memberships = list(
            HouseholdMembership.objects.select_related("household")
            .filter(user=request.user)
            .order_by("household__name")
        )
        active_id = getattr(request.user, "_active_household_id", None)
        current = next((item for item in memberships if str(item.household_id) == active_id), None)
        current_household = (current or (memberships[0] if memberships else None))
    is_household_owner = (
        current_household is not None
        and current_household.role == HouseholdMembership.Role.OWNER
    )
    return {
        "current_household": current_household.household if current_household else None,
        "household_memberships": memberships,
        "is_household_owner": is_household_owner,
    }
