from .models import HouseholdMembership


def owner_navigation(request):
    is_household_owner = (
        request.user.is_authenticated
        and HouseholdMembership.objects.filter(
            user=request.user,
            role=HouseholdMembership.Role.OWNER,
        ).exists()
    )
    return {"is_household_owner": is_household_owner}
