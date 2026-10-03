from mikronous import deliver


def test_strip_cron_wrapper_reminder():
    wrapped = ('Cronjob Response: Reminder: Stretch\n(job_id: f57ee14ce8a9)\n-------------\n\nStretch\n\n'
               'To stop or manage this job, send me a new message (e.g. "stop reminder Reminder: Stretch").')
    assert deliver._strip_cron_wrapper(wrapped) == ("Reminder", "Stretch")


def test_strip_cron_wrapper_other_job_and_plain_text():
    assert deliver._strip_cron_wrapper("Cronjob Response: Daily briefing\n(job_id: x)\n-------------\n\nLine 1\nLine 2\n\n"
                                       "To stop or manage this job, send me a new message (e.g. \"stop reminder Daily briefing\").") \
        == ("Daily briefing", "Line 1\nLine 2")
    assert deliver._strip_cron_wrapper("♻ Gateway online") == (None, "♻ Gateway online")
