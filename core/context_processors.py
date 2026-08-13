from .models import Household, HouseholdMembership
from .services import is_global_admin


def owner_navigation(request):
    memberships = []
    current_household = None
    if request.user.is_authenticated:
        memberships = list(
            HouseholdMembership.objects.select_related("household")
            .filter(user=request.user)
            .order_by("household__name")
        )
        if is_global_admin(request.user):
            memberships = list(
                HouseholdMembership.objects.select_related("household", "user")
                .filter(user=request.user)
                .order_by("household__name")
            )
            known = {membership.household_id for membership in memberships}
            memberships.extend(
                HouseholdMembership(
                    household=household,
                    user=request.user,
                    role=HouseholdMembership.Role.OWNER,
                )
                for household in Household.objects.exclude(id__in=known).order_by("name")
            )
        active_id = getattr(request.user, "_active_household_id", None)
        current = next((item for item in memberships if str(item.household_id) == active_id), None)
        current_household = (current or (memberships[0] if memberships else None))
    is_household_admin = (
        current_household is not None
        and (
            current_household.role
            in [HouseholdMembership.Role.OWNER, HouseholdMembership.Role.ADMIN]
            or is_global_admin(request.user)
        )
    )
    is_household_owner = (
        current_household is not None
        and (
            current_household.role == HouseholdMembership.Role.OWNER
            or is_global_admin(request.user)
        )
    )
    return {
        "current_household": current_household.household if current_household else None,
        "household_memberships": memberships,
        "is_household_owner": is_household_owner,
        "is_household_admin": is_household_admin,
    }
