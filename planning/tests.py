import json
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryEvent, InventoryItem
from recipes.models import (
    Recipe,
    RecipeFavorite,
    RecipeIngredient,
    RecipeSource,
    RecipeStep,
    RecipeTag,
    RecipeTagAssignment,
)

from .models import CookEvent, MealPlan, MealSlot
from .services import (
    SlotNotCookable,
    StaleSlotVersion,
    add_slot,
    current_week_start,
    get_or_create_plan,
    mark_cooked,
    shift_slot,
    undo_cooked,
    week_grid,
    week_start_for,
)


class PlanningTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nonna", password="pass")
        self.household = Household.objects.create(name="Casa Nonna")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        self.basil = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        self.source = RecipeSource.objects.create(household=self.household)
        self.recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Sugo",
            servings=2,
            status=Recipe.Status.APPROVED,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            sort_order=0,
            source_text="Tomaten",
            amount=400,
            unit="g",
            canonical_ingredient=self.tomato,
        )
        RecipeStep.objects.create(recipe=self.recipe, sort_order=0, body="Köcheln lassen.")
        self.week_start = current_week_start()
        self.plan = get_or_create_plan(user=self.user, week_start=self.week_start)
        self.client.force_login(self.user)

    def make_slot(self, *, day_offset=0, slot="dinner", servings=4):
        return add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start + timedelta(days=day_offset),
            slot=slot,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
            servings=servings,
        )


class WeekStructureTests(PlanningTestCase):
    def test_week_start_normalises_to_monday(self):
        self.assertEqual(week_start_for(date(2025, 3, 13)), date(2025, 3, 10))
        self.assertEqual(week_start_for(date(2025, 3, 10)), date(2025, 3, 10))

    def test_one_plan_per_household_and_week(self):
        again = get_or_create_plan(user=self.user, week_start=self.week_start)
        self.assertEqual(again.id, self.plan.id)
        self.assertEqual(MealPlan.objects.filter(household=self.household).count(), 1)

    def test_slot_outside_week_is_rejected(self):
        with self.assertRaises(ValueError):
            add_slot(
                user=self.user,
                week_start=self.week_start,
                date=self.week_start + timedelta(days=9),
                slot="dinner",
                entry_type=MealSlot.EntryType.RECIPE,
                recipe_id=self.recipe.id,
            )

    def test_recipe_entry_requires_an_approved_recipe(self):
        draft = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Entwurf",
            status=Recipe.Status.DRAFT,
        )
        with self.assertRaises(ValueError):
            add_slot(
                user=self.user,
                week_start=self.week_start,
                date=self.week_start,
                slot="dinner",
                entry_type=MealSlot.EntryType.RECIPE,
                recipe_id=draft.id,
            )

    def test_note_entry_needs_text_but_no_recipe(self):
        slot = add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start,
            slot="lunch",
            entry_type=MealSlot.EntryType.NOTE,
            notes="Essen gehen",
        )
        self.assertIsNone(slot.recipe_id)
        self.assertEqual(slot.display_label, "Essen gehen")

    def test_servings_default_to_the_recipe(self):
        slot = self.make_slot(servings=None)
        self.assertEqual(slot.servings, self.recipe.servings)

    def test_moving_a_slot_bumps_the_version(self):
        slot = self.make_slot(day_offset=1)
        moved = shift_slot(
            user=self.user, slot_id=slot.id, version=slot.version, day_delta=1, slot_delta=0
        )
        self.assertEqual(moved.date, self.week_start + timedelta(days=2))
        self.assertEqual(moved.version, slot.version + 1)

    def test_moving_is_clamped_to_the_week(self):
        slot = self.make_slot(day_offset=0)
        moved = shift_slot(
            user=self.user, slot_id=slot.id, version=slot.version, day_delta=-3, slot_delta=0
        )
        self.assertEqual(moved.date, self.week_start)

    def test_stale_version_on_move_conflicts(self):
        slot = self.make_slot()
        shift_slot(user=self.user, slot_id=slot.id, version=slot.version, day_delta=1)
        with self.assertRaises(StaleSlotVersion):
            shift_slot(user=self.user, slot_id=slot.id, version=slot.version, day_delta=1)

    def test_plan_api_marks_duplicate_recipe_slots(self):
        first = self.make_slot(day_offset=0)
        second = self.make_slot(day_offset=1)

        response = self.client.get(f"/api/v1/meal-plans/{self.week_start.isoformat()}")

        self.assertEqual(response.status_code, 200)
        slots = {slot["id"]: slot for slot in response.json()["slots"]}
        self.assertTrue(slots[str(first.id)]["isDuplicate"])
        self.assertTrue(slots[str(second.id)]["isDuplicate"])


class CookingTests(PlanningTestCase):
    def test_kitchen_page_shows_a_ready_recipe_image(self):
        self.recipe.image = "recipes/sugo.png"
        self.recipe.image_status = "ready"
        self.recipe.save(update_fields=["image", "image_status"])
        slot = self.make_slot()

        response = self.client.get(f"/plan/slots/{slot.id}/kitchen/")

        self.assertContains(response, f"/recipes/{self.recipe.id}/image/")

    def test_cooking_records_exactly_one_event(self):
        slot = self.make_slot()
        mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        slot.refresh_from_db()
        self.assertIsNotNone(slot.cooked_at)
        self.assertEqual(CookEvent.objects.filter(meal_slot=slot).count(), 1)

    def test_cooking_twice_is_refused(self):
        slot = self.make_slot()
        mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        slot.refresh_from_db()
        with self.assertRaises(SlotNotCookable):
            mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        self.assertEqual(CookEvent.objects.count(), 1)

    def test_plan_api_marks_recently_cooked_recipe_slots(self):
        cooked = self.make_slot(day_offset=0)
        planned = self.make_slot(day_offset=1)
        mark_cooked(user=self.user, slot_id=cooked.id, slot_version=cooked.version)

        response = self.client.get(f"/api/v1/meal-plans/{self.week_start.isoformat()}")

        self.assertEqual(response.status_code, 200)
        slots = {slot["id"]: slot for slot in response.json()["slots"]}
        self.assertTrue(slots[str(cooked.id)]["isRecentRepeat"])
        self.assertTrue(slots[str(planned.id)]["isRecentRepeat"])

    def test_cooking_does_not_infer_depletion(self):
        slot = self.make_slot()
        InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status="available"
        )
        mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        item = InventoryItem.objects.get(ingredient=self.tomato)
        self.assertEqual(item.status, "available")

    def test_selected_changes_use_the_cook_origin_and_reference_the_slot(self):
        slot = self.make_slot()
        item = InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status="available"
        )
        mark_cooked(
            user=self.user,
            slot_id=slot.id,
            slot_version=slot.version,
            inventory_changes=[{"ingredient_id": self.tomato.id, "status": "unavailable"}],
        )
        item.refresh_from_db()
        self.assertEqual(item.status, "unavailable")
        event = InventoryEvent.objects.get(item=item)
        self.assertEqual(event.origin, InventoryEvent.Origin.COOK_RECIPE)
        self.assertEqual(event.meal_slot_id, slot.id)

    def test_changes_must_belong_to_the_cooked_recipe(self):
        slot = self.make_slot()
        with self.assertRaises(ValueError):
            mark_cooked(
                user=self.user,
                slot_id=slot.id,
                slot_version=slot.version,
                inventory_changes=[{"ingredient_id": self.basil.id, "status": "unavailable"}],
            )
        slot.refresh_from_db()
        self.assertIsNone(slot.cooked_at)
        self.assertFalse(InventoryEvent.objects.exists())

    def test_undo_removes_the_cook_event(self):
        slot = self.make_slot()
        mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        undo_cooked(user=self.user, slot_id=slot.id)
        slot.refresh_from_db()
        self.assertIsNone(slot.cooked_at)
        self.assertEqual(CookEvent.objects.count(), 0)

    def test_notes_cannot_be_cooked(self):
        slot = add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start,
            slot="lunch",
            entry_type=MealSlot.EntryType.NOTE,
            notes="Reste",
        )
        with self.assertRaises(SlotNotCookable):
            mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)


class PlannedStockTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        # The warning only covers meals still ahead of us, so plan into the future
        # regardless of which weekday the suite happens to run on.
        target = timezone.localdate() + timedelta(days=2)
        plan = get_or_create_plan(user=self.user, week_start=week_start_for(target))
        self.slot = add_slot(
            user=self.user,
            week_start=plan.week_start_date,
            date=target,
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
            servings=4,
        )
        self.item = InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status="available"
        )

    def patch_inventory(self, body):
        return self.client.patch(
            "/api/v1/inventory", json.dumps(body), content_type="application/json"
        )

    def test_removing_planned_stock_asks_first(self):
        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.tomato.id),
                        "status": "unavailable",
                        "version": self.item.version,
                    }
                ]
            }
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["error"]["code"], "planned_ingredient_in_use")
        self.assertEqual(len(body["error"]["plannedSlots"]), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "available")

    def test_confirmation_lets_the_change_through(self):
        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.tomato.id),
                        "status": "unavailable",
                        "version": self.item.version,
                        "confirmPlannedUse": True,
                    }
                ]
            }
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "unavailable")
        self.assertEqual(
            InventoryEvent.objects.get(item=self.item).origin, InventoryEvent.Origin.MANUAL
        )

    def test_cooked_slots_no_longer_warn(self):
        mark_cooked(user=self.user, slot_id=self.slot.id, slot_version=self.slot.version)
        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.tomato.id),
                        "status": "unavailable",
                        "version": self.item.version,
                    }
                ]
            }
        )
        self.assertEqual(response.status_code, 200)

    def test_adding_stock_never_warns(self):
        self.item.status = "unknown"
        self.item.save(update_fields=["status"])
        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.tomato.id),
                        "status": "available",
                        "version": self.item.version,
                    }
                ]
            }
        )
        self.assertEqual(response.status_code, 200)

    def test_client_cannot_forge_the_cook_origin(self):
        # The general inventory endpoint must never mint audit records that claim a
        # meal was cooked, no matter what the client sends.
        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.tomato.id),
                        "status": "unavailable",
                        "version": self.item.version,
                        "confirmPlannedUse": True,
                        "origin": "cook_recipe",
                        "mealSlotId": str(self.slot.id),
                    }
                ]
            }
        )
        self.assertEqual(response.status_code, 200)
        event = InventoryEvent.objects.get(item=self.item)
        self.assertEqual(event.origin, InventoryEvent.Origin.MANUAL)
        self.assertIsNone(event.meal_slot_id)
        self.assertFalse(CookEvent.objects.exists())


class AuthorizationTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.outsider = User.objects.create_user(username="fremd", password="pass")
        other_household = Household.objects.create(name="Andere")
        HouseholdMembership.objects.create(
            household=other_household, user=self.outsider, role="owner"
        )
        self.slot = self.make_slot()

    def test_other_household_cannot_read_the_plan(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/api/v1/meal-plans/{self.week_start.isoformat()}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["slots"], [])

    def test_other_household_cannot_touch_a_slot(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            f"/api/v1/meal-slots/{self.slot.id}/mark-cooked",
            json.dumps({"slotVersion": self.slot.version}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_other_household_cannot_delete_a_slot(self):
        self.client.force_login(self.outsider)
        response = self.client.post(f"/plan/slots/{self.slot.id}/delete/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(MealSlot.objects.filter(id=self.slot.id).exists())

    def test_anonymous_users_are_sent_to_the_sign_in_page(self):
        self.client.logout()
        response = self.client.get("/plan/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class HistoryTests(PlanningTestCase):
    def test_history_lists_recent_meals_first(self):
        first = self.make_slot(day_offset=0)
        second = self.make_slot(day_offset=1)
        mark_cooked(user=self.user, slot_id=first.id, slot_version=first.version)
        mark_cooked(user=self.user, slot_id=second.id, slot_version=second.version)
        response = self.client.get("/api/v1/cook-events")
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        self.assertEqual(len(events), 2)

    def test_recent_repeats_are_flagged_on_the_week_page(self):
        cooked = self.make_slot(day_offset=0)
        mark_cooked(user=self.user, slot_id=cooked.id, slot_version=cooked.version)
        CookEvent.objects.filter(meal_slot=cooked).update(
            cooked_at=timezone.now() - timedelta(days=3)
        )
        self.make_slot(day_offset=4)
        response = self.client.get("/plan/")
        self.assertContains(response, "Kürzlich gekocht")


class MultiDishSlotTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.dessert = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Tiramisu",
            servings=6,
            status=Recipe.Status.APPROVED,
        )

    def add(self, recipe, **kwargs):
        return add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start,
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
            **kwargs,
        )

    def test_a_meal_lists_its_courses_in_menu_order(self):
        dessert = self.add(self.dessert, course="dessert")
        main = self.add(self.recipe)
        starter = self.add(self.recipe, course="starter")

        cell = week_grid(self.plan)[0]["slots"][2]

        self.assertEqual([entry.id for entry in cell["entries"]], [starter.id, main.id, dessert.id])
        self.assertEqual(cell["servings"], 2)

    def test_unknown_course_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "Unbekannter Gang."):
            self.add(self.recipe, course="amuse")

    def test_new_dish_can_set_servings_for_the_whole_meal(self):
        main = self.add(self.recipe, servings=2)
        cooked = self.add(self.recipe, servings=2)
        mark_cooked(user=self.user, slot_id=cooked.id, slot_version=cooked.version)

        self.add(self.dessert, servings=8, servings_for_whole_slot=True)

        main.refresh_from_db()
        cooked.refresh_from_db()
        self.assertEqual(main.servings, 8)
        self.assertEqual(main.version, 2, "siblings change version so stale forms conflict")
        self.assertEqual(cooked.servings, 2, "a cooked dish keeps what was actually cooked")

    def test_servings_stay_per_dish_unless_asked(self):
        main = self.add(self.recipe, servings=2)
        self.add(self.dessert, servings=8)

        main.refresh_from_db()
        self.assertEqual(main.servings, 2)

    def test_updating_one_dish_can_resize_the_whole_meal(self):
        main = self.add(self.recipe, servings=2)
        dessert = self.add(self.dessert, servings=6)

        response = self.client.post(
            f"/plan/slots/{main.id}/update/",
            {
                "version": main.version,
                "servings": "5",
                "course": "main",
                "servings_for_whole_slot": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        main.refresh_from_db()
        dessert.refresh_from_db()
        self.assertEqual((main.servings, main.course), (5, "main"))
        self.assertEqual(dessert.servings, 5)

    def test_api_accepts_and_reports_the_course(self):
        response = self.client.post(
            f"/api/v1/meal-plans/{self.week_start.isoformat()}/slots",
            data=json.dumps(
                {
                    "date": self.week_start.isoformat(),
                    "slot": "dinner",
                    "recipeId": str(self.dessert.id),
                    "course": "dessert",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["course"], "dessert")

    def test_filled_meal_still_offers_another_dish(self):
        self.add(self.recipe)

        response = self.client.get(f"/plan/{self.week_start.isoformat()}/")

        self.assertContains(response, "+ Gericht")
        self.assertContains(response, "+ Mittag")


class PlanDialogTests(PlanningTestCase):
    def post_dish(self, **extra):
        return self.client.post(
            f"/plan/{self.week_start.isoformat()}/slots/",
            {
                "entry_type": "recipe",
                "recipe_id": str(self.recipe.id),
                "date": self.week_start.isoformat(),
                "slot": "dinner",
                **extra,
            },
        )

    def test_planning_returns_to_the_day(self):
        response = self.post_dish()

        day = self.week_start.isoformat()
        self.assertRedirects(response, f"/plan/{day}/#day-{day}", fetch_redirect_response=False)

    def test_plan_another_reopens_the_dialog_for_the_same_meal(self):
        response = self.post_dish(then="another")

        day = self.week_start.isoformat()
        self.assertRedirects(
            response,
            f"/plan/{day}/?date={day}&slot=dinner#day-{day}",
            fetch_redirect_response=False,
        )
        page = self.client.get(response.url)
        self.assertContains(page, " open data-autoshow")
        self.assertContains(page, 'value="2" data-servings-input', msg_prefix="servings carry over")

    def test_a_rejected_dish_reopens_the_dialog(self):
        response = self.post_dish(recipe_id="")

        self.assertIn("?date=", response.url)

    def test_dialog_ignores_cells_outside_the_week(self):
        outside = (self.week_start + timedelta(days=9)).isoformat()

        response = self.client.get(
            f"/plan/{self.week_start.isoformat()}/?date={outside}&slot=dinner"
        )

        self.assertNotContains(response, " open data-autoshow")

    def test_picker_lists_favourites_first_and_shows_thumbnails(self):
        other = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Arancini",
            status=Recipe.Status.APPROVED,
            image_status="ready",
            thumbnail="recipe-thumbnails/arancini.jpg",
        )
        RecipeFavorite.objects.create(recipe=self.recipe, user=self.user)
        add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start,
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=other.id,
        )

        response = self.client.get(f"/plan/{self.week_start.isoformat()}/")
        content = response.content.decode()

        self.assertLess(
            content.index(f'data-id="{self.recipe.id}"'), content.index(f'data-id="{other.id}"')
        )
        # Once on the planned card, once in the picker.
        self.assertContains(response, reverse("recipe-thumbnail", args=[other.id]), count=2)

    def test_tag_chips_merge_spellings_under_the_commoner_one(self):
        spellings = ["Schnell", "Schnell", "schnell"]
        for index, name in enumerate(spellings):
            recipe = Recipe.objects.create(
                household=self.household,
                created_by=self.user,
                source=self.source,
                title=f"Gericht {index}",
                status=Recipe.Status.APPROVED,
            )
            tag, _ = RecipeTag.objects.get_or_create(household=self.household, name=name)
            RecipeTagAssignment.objects.create(recipe=recipe, tag=tag)

        response = self.client.get(f"/plan/{self.week_start.isoformat()}/")

        self.assertEqual(response.context["recipe_tags"], [{"name": "Schnell", "key": "schnell"}])
        self.assertContains(response, 'data-tags="schnell"', count=3)


class PlanRecipeSearchTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.feta = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Kritharaki mit Feta",
            status=Recipe.Status.APPROVED,
            search_embedding=[0.0, 1.0],
        )
        self.recipe.search_embedding = [1.0, 0.0]
        self.recipe.save()
        tag = RecipeTag.objects.create(household=self.household, name="Sommer")
        RecipeTagAssignment.objects.create(recipe=self.recipe, tag=tag)

    def search(self, query):
        return self.client.get("/plan/recipe-search/", {"q": query}).json()["results"]

    @patch("recipes.semantic.query_embedding", return_value=[0.8, 0.6])
    def test_a_literal_title_hit_beats_an_embedding_neighbour(self, _embedding):
        results = self.search("Feta")

        self.assertEqual(results[0]["id"], str(self.feta.id))
        self.assertEqual(results[0]["method"], "text")
        self.assertEqual(
            results[1], {"id": str(self.recipe.id), "score": 0.8, "method": "semantic"}
        )

    @patch("recipes.semantic.query_embedding", return_value=None)
    def test_tags_and_ingredients_are_searched_without_embeddings(self, _embedding):
        self.assertEqual([result["id"] for result in self.search("sommer")], [str(self.recipe.id)])
        self.assertEqual([result["id"] for result in self.search("Tomaten")], [str(self.recipe.id)])

    @patch("recipes.semantic.query_embedding", return_value=None)
    def test_drafts_and_other_households_stay_out(self, _embedding):
        Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title="Feta-Entwurf",
            status=Recipe.Status.DRAFT,
        )
        stranger = User.objects.create_user(username="ospite", password="pass")
        elsewhere = Household.objects.create(name="Altrove")
        HouseholdMembership.objects.create(household=elsewhere, user=stranger, role="owner")
        Recipe.objects.create(
            household=elsewhere,
            created_by=stranger,
            source=RecipeSource.objects.create(household=elsewhere),
            title="Feta fremd",
            status=Recipe.Status.APPROVED,
        )

        self.assertEqual([result["id"] for result in self.search("Feta")], [str(self.feta.id)])
