from unittest.mock import patch

from django.test import SimpleTestCase

from .management.commands.worker import Command


class WorkerTelemetryTests(SimpleTestCase):
    @patch("core.management.commands.worker.log_event")
    @patch("core.management.commands.worker.run_next_ingredient_icon_job")
    @patch("core.management.commands.worker.run_next_recipe_image_job")
    @patch("core.management.commands.worker.run_next_recipe_generation_job")
    @patch("core.management.commands.worker.run_next_category_job")
    def test_processed_job_emits_uniform_execution_entry(
        self, category_runner, generation_runner, recipe_runner, icon_runner, log_event
    ):
        category_runner.return_value = False
        generation_runner.return_value = False
        recipe_runner.return_value = True

        processed = Command().run_next_job()

        self.assertTrue(processed)
        category_runner.assert_called_once_with()
        generation_runner.assert_called_once_with()
        recipe_runner.assert_called_once_with()
        icon_runner.assert_not_called()
        self.assertTrue(
            any(
                call.args[1] == "worker.job_execution_completed"
                and call.kwargs["job_type"] == "recipe_image"
                and isinstance(call.kwargs["duration_ms"], int)
                for call in log_event.call_args_list
            )
        )

    @patch("core.management.commands.worker.log_event")
    @patch("core.management.commands.worker.run_next_category_job")
    def test_unexpected_runner_error_emits_failure_entry(self, category_runner, log_event):
        category_runner.side_effect = RuntimeError("unexpected")

        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            Command().run_next_job()

        self.assertTrue(
            any(
                call.args[1] == "worker.job_execution_failed"
                and call.kwargs["job_type"] == "pantry_category"
                and isinstance(call.kwargs["duration_ms"], int)
                for call in log_event.call_args_list
            )
        )