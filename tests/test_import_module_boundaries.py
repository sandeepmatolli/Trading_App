"""Prevent accidental replacement of batch-runner source with campaign source."""


def test_batch_runner_exports_are_defined_in_correct_module():
    from screening import batch_runner

    assert batch_runner.IST.key == "Asia/Kolkata"
    assert batch_runner.ScanSafetyError.__module__ == "screening.batch_runner"
    assert batch_runner.load_inputs.__module__ == "screening.batch_runner"
    assert batch_runner.run_session.__module__ == "screening.batch_runner"
    assert batch_runner.verify_input_integrity.__module__ == "screening.batch_runner"
    assert "screening/campaign.py" in batch_runner.CODE_FILES
    assert "market_data/setup_history_gate.py" in batch_runner.CODE_FILES


def test_campaign_exports_are_defined_in_campaign_module():
    from screening import campaign

    assert campaign.record_session.__module__ == "screening.campaign"
    assert campaign.prepare_day.__module__ == "screening.campaign"
    assert campaign.verify_previous_campaign_checkpoint.__module__ == "screening.campaign"
    assert callable(campaign.historical_summary)