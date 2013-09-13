import json
import os

import redis
import requests

MESSAGES_URL = 'https://api.twilio.com/2010-04-01/Accounts/{acct_sid}/Messages'
TWILIO_SHORTCODE = '894546'


def send_message(to, from_, body=None, media_urls=None):
    """A really dumb reimplementation of a Twilio client for MMS.

    Because we can't publish the real deal yet so we can't run it in Heroku,
    that's why.
    """
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    sender_account = os.getenv('SENDER_ACCOUNT', account_sid)

    if body is None and media_urls is None:
        raise ValueError("Need to specify at least one of body, media_urls")

    params = {
        'To': to,
        'From': from_,
        'Body': body,
        'MediaUrl': media_urls,
    }

    res = requests.post(
        MESSAGES_URL.format(acct_sid=sender_account),
        auth=(account_sid, auth_token),
        params=params,
    )

    if res.status_code != 201:
        raise ValueError("Error sending message: {}".format(res.content))


def send_directions_page(recipient, page_size):
    redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'))

    steps = redis_client.lrange(recipient, 0, page_size - 1)
    redis_client.lrem(recipient, page_size)
    head, tail = steps[:-1], steps[-1]
    for step in head:
        decoded = json.loads(step)
        send_message(
            recipient,
            TWILIO_SHORTCODE,
            body=decoded['text'],
            media_urls=[decoded['image']],
        )

    decoded = json.loads(tail)
    if redis_client.llen(recipient) > 0:
        body = '{} (Reply "next" for next page)'.format(decoded['text'])
    else:
        body = decoded['text']

    send_message(
        recipient,
        TWILIO_SHORTCODE,
        body=body,
        media_urls=[decoded['image']],
    )
