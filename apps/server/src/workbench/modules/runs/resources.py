"""Shared native-resource cleanup contract for task cancellation and completion."""


def request_native_cleanup(db, run_id):
    from workbench.modules.automation.service import request_cleanup as automation
    from workbench.modules.processes.service import request_cleanup as commands

    return automation(db, run_id) + commands(db, run_id)


def native_cleanup_pending(db, run_id):
    from workbench.modules.automation.service import cleanup_pending as automation
    from workbench.modules.processes.service import cleanup_pending as commands

    return automation(db, run_id) or commands(db, run_id)
