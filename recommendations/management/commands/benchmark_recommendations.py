import math
import statistics
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Household, HouseholdMembership
from planning.services import current_week_start, parse_week_start

from ...contracts import RecommendationOptions
from ...services import recommend


def percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


class Command(BaseCommand):
    help = "Benchmark uncached catalog-v1 recommendation assembly and scoring."

    def add_arguments(self, parser):
        parser.add_argument("--household")
        parser.add_argument("--iterations", type=int, default=5)
        parser.add_argument("--week-start")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        household_query = Household.objects.all()
        if options["household"]:
            household_query = household_query.filter(id=options["household"])
        household = household_query.first()
        if not household:
            raise CommandError("No matching household exists.")
        membership = (
            HouseholdMembership.objects.select_related("user")
            .filter(household=household)
            .first()
        )
        if not membership:
            raise CommandError("The household has no member to request recommendations.")
        iterations = options["iterations"]
        if iterations < 1:
            raise CommandError("--iterations must be positive.")
        try:
            week_start = (
                parse_week_start(options["week_start"])
                if options["week_start"]
                else current_week_start()
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        query_counts = []
        query_times = []
        scoring_times = []
        total_times = []
        candidate_count = 0
        for _ in range(iterations):
            started = time.perf_counter_ns()
            with CaptureQueriesContext(connection) as captured:
                result = recommend(
                    user=membership.user,
                    options=RecommendationOptions(
                        week_start=week_start,
                        limit=options["limit"],
                    ),
                )
            total_times.append((time.perf_counter_ns() - started) / 1_000_000)
            query_counts.append(len(captured))
            query_times.append(result.query_duration_ms)
            scoring_times.append(result.scoring_duration_ms)
            candidate_count = result.candidate_count

        def measurements(values):
            return (
                f"median={statistics.median(values):.2f}ms "
                f"p95={percentile(values, 0.95):.2f}ms"
            )

        self.stdout.write(f"household={household.id} candidates={candidate_count}")
        self.stdout.write(
            f"sql_count median={statistics.median(query_counts):.1f} "
            f"p95={percentile(query_counts, 0.95)}"
        )
        self.stdout.write(f"query {measurements(query_times)}")
        self.stdout.write(f"scoring {measurements(scoring_times)}")
        self.stdout.write(f"total {measurements(total_times)}")
