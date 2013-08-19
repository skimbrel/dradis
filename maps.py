import flask
import redis
from flask import request
from geopy import geocoders
from urllib import urlencode


app = flask.Flask(__name__)

geocoder = geocoders.GoogleV3()
redis_client = redis.from_url('redis://localhost:6379')

STATIC_MAPS_URI = 'https://maps.googleapis.com/maps/api/staticmap'
DEFAULT_MAPS_PARAMS = {'sensor': 'false', 'size': '640x640'}
GOOGLE_MAPS_URI = 'http://maps.googleapis.com/maps/api/directions/json?origin='
STREETVIEW_URI = 'http://maps.googleapis.com/maps/api/streetview?'

@app.route('/', methods=['POST'])
def get_map():
    phone_number = request.form['From']
    body = request.form['Body']

    place, (lat, lon) = geocoder.geocode(body)
    params = {
        'center': '{},{}'.format(str(lat), str(lon)),
        'zoom': 10,
    }
    params.update(DEFAULT_MAPS_PARAMS)
    response = u'''
<Response>
    <Message>
        <Media>
            {}?{}
        </Media>
    </Message>
</Response>'''.format(STATIC_MAPS_URI, urlencode(params))

    return response


def _get_stored_location(phone_number):
    return redis_client.hgetall(phone_number)


def _store_location(phone_number, lat, lon, zoom):
    redis_client.hmset(
        phone_number,
        {
            'lat': lat,
            'lon': lon,
            'zoom': zoom,
        },
    )


if __name__ == '__main__':
    app.debug = True
    app.run()
