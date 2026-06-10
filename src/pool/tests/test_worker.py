import contextlib
from unittest.mock import MagicMock, patch

from django.db.utils import InterfaceError, OperationalError
from django.test import SimpleTestCase

from src.pool.management.commands.run_projection_worker import Command


class _StopTest(BaseException):
    """Sinal de parada de loop que não é capturado por 'except Exception'."""


class WorkerConnectionRecoveryTest(SimpleTestCase):
    """Verifica que o worker fecha conexões mortas e não trava após DB drop."""

    def _run_worker(self, side_effects):
        """
        Executa o loop do worker consumindo side_effects em ordem.
        Lança _StopTest depois do último efeito para parar o loop.
        Retorna mock de close_old_connections.
        """
        effects = list(side_effects) + [_StopTest()]
        idx = [0]

        def mock_process():
            effect = effects[idx[0]]
            idx[0] += 1
            if isinstance(effect, BaseException):
                raise effect
            return effect

        cmd = Command()
        with (
            patch(
                "src.pool.management.commands.run_projection_worker.process_next_projection_recalc_job",
                side_effect=mock_process,
            ),
            patch("src.pool.management.commands.run_projection_worker.close_old_connections") as mock_close,
            patch("time.sleep"),
            contextlib.suppress(_StopTest),
        ):
            cmd.handle(sleep=0)

        return mock_close

    def test_close_called_before_first_db_access(self):
        """close_old_connections chamado antes de process_next_projection_recalc_job."""
        call_order = []

        def mock_close():
            call_order.append("close")

        def mock_process():
            call_order.append("process")
            raise _StopTest

        cmd = Command()
        with (
            patch(
                "src.pool.management.commands.run_projection_worker.process_next_projection_recalc_job",
                side_effect=mock_process,
            ),
            patch(
                "src.pool.management.commands.run_projection_worker.close_old_connections",
                side_effect=mock_close,
            ),
            patch("time.sleep"),
            contextlib.suppress(_StopTest),
        ):
            cmd.handle(sleep=0)

        self.assertEqual(call_order[0], "close", "close deve preceder o primeiro acesso ao DB")
        self.assertEqual(call_order[1], "process")

    def test_close_old_connections_called_every_iteration(self):
        """close_old_connections chamado no início de cada iteração normal."""
        mock_close = self._run_worker([None, None, None])
        # Pelo menos 3 chamadas (uma por iteração) + 1 da iteração que lança _StopTest
        self.assertGreaterEqual(mock_close.call_count, 3)

    def test_close_called_after_operational_error(self):
        """Após OperationalError (DB drop), close_old_connections chamado no except."""
        call_sequence = []

        def mock_close():
            call_sequence.append("close")

        def mock_process():
            if len([x for x in call_sequence if x == "close"]) <= 1:
                call_sequence.append("process_error")
                raise OperationalError("server closed the connection unexpectedly")
            call_sequence.append("process_ok")
            raise _StopTest

        cmd = Command()
        with (
            patch(
                "src.pool.management.commands.run_projection_worker.process_next_projection_recalc_job",
                side_effect=mock_process,
            ),
            patch(
                "src.pool.management.commands.run_projection_worker.close_old_connections",
                side_effect=mock_close,
            ),
            patch("time.sleep"),
            contextlib.suppress(_StopTest),
        ):
            cmd.handle(sleep=0)

        error_pos = next(i for i, v in enumerate(call_sequence) if v == "process_error")
        close_after_error = [i for i, v in enumerate(call_sequence) if v == "close" and i > error_pos]
        self.assertTrue(close_after_error, "close_old_connections não chamado após OperationalError")

    def test_worker_survives_interface_error(self):
        """Worker continua executando após InterfaceError (conexão já fechada)."""
        job_mock = MagicMock()
        job_mock.participant_id = 42
        job_mock.status = "IDLE"

        # Primeiro: InterfaceError; Segundo: job processado; Terceiro: para
        mock_close = self._run_worker([InterfaceError("connection already closed"), job_mock])

        # close deve ter sido chamado pelo menos na iteração do erro (except) + próximas
        self.assertGreaterEqual(mock_close.call_count, 2)

    def test_close_called_after_interface_error(self):
        """Após InterfaceError, close_old_connections chamado no except antes de continuar."""
        call_sequence = []

        def mock_close():
            call_sequence.append("close")

        def mock_process():
            call_sequence.append("process")
            if call_sequence.count("process") == 1:
                raise InterfaceError("connection already closed")
            raise _StopTest

        cmd = Command()
        with (
            patch(
                "src.pool.management.commands.run_projection_worker.process_next_projection_recalc_job",
                side_effect=mock_process,
            ),
            patch(
                "src.pool.management.commands.run_projection_worker.close_old_connections",
                side_effect=mock_close,
            ),
            patch("time.sleep"),
            contextlib.suppress(_StopTest),
        ):
            cmd.handle(sleep=0)

        first_process = next(i for i, v in enumerate(call_sequence) if v == "process")
        close_after_error = [i for i, v in enumerate(call_sequence) if v == "close" and i > first_process]
        self.assertTrue(close_after_error, "close_old_connections não chamado no except após InterfaceError")
