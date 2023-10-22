import datetime

def get_this_week_start_date():
    today = datetime.date.today()
    week_start_date = today - datetime.timedelta(days = today.weekday())
    return week_start_date

def get_next_week_start_date():
    return get_this_week_start_date() + datetime.timedelta(weeks = 1)
