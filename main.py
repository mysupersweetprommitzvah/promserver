#!/usr/bin/env python

import webapp2
import hashlib
import requests
import logging
import random
import time
import datetime
import json
from google.appengine.ext import db
from twilio.rest import TwilioRestClient

TW_FROM = "REDACTED"
TW_SID = "REDACTED"
TW_TOKEN = "REDACTED"


client = TwilioRestClient(TW_SID, TW_TOKEN)


def send_sms(number, body):
    url = "https://api.twilio.com/2010-04-01/Accounts/%s/SMS/Messages.json" % TW_SID
    logging.info("Trying to send")
    r = requests.post(url, data={
        "To": number,
        "From": TW_FROM,
        "Body": body
    }, auth=(TW_SID, TW_TOKEN))
    logging.info("Reading response")
    print "GOT RESPONSE:"
    r.raw.read()
    logging.info("Done.")


class PhoneUser(db.Model):
    full_name = db.StringProperty(required=True)
    phone_number = db.StringProperty(required=False)
    confirmed = db.BooleanProperty(default=False)
    email = db.StringProperty(required=False)


class AchievementSpec(db.Model):
    achievement_key = db.StringProperty(required=True)
    name = db.StringProperty(required=True)
    description = db.StringProperty(required=False)
    gain_message = db.StringProperty(required=False)
    point_value = db.IntegerProperty(required=True)


class Achievement(db.Model):
    user = db.ReferenceProperty(reference_class=PhoneUser)
    achievement_spec = db.ReferenceProperty(reference_class=AchievementSpec)
    notified = db.BooleanProperty(default=False)


class AchievementCode(db.Model):
    achievement_spec = db.ReferenceProperty(reference_class=AchievementSpec)
    code = db.StringProperty(required=True)
    redeemed = db.BooleanProperty(default=False)


class GrantingApp(db.Model):
    achievement_spec = db.ReferenceProperty(reference_class=AchievementSpec)
    code = db.StringProperty(required=True)
    url = db.StringProperty(required=True)


class MainHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write("This is the Prom Achievements Server. Go away.")


class RedeemHandler(webapp2.RequestHandler):
    def post(self):
        from_number = self.request.get('From')
        try:
            user = PhoneUser.all().filter('phone_number =', from_number)[0]
        except IndexError:
            return self.response.write(build_text_response(
                "Sorry, we don't recognize your phone number."
            ))
        code_key = self.request.get('Body').strip().replace(" ", "").lower()
        try:
            code = AchievementCode.all().filter('code =', code_key)[0]
        except IndexError:
            return self.response.write(build_text_response(
                "Hmm, I don't recognize that code."
            ))

        if code.redeemed:
            return self.response.write(build_text_response(
                "Sorry, that code's been redeemed already."
            ))

        try:
            Achievement.all().filter('user =', user)[0]
        except IndexError:
            pass
        else:
            return self.response.write(build_text_response(
                "Whoops, you've already got that achievement."
            ))

        Achievement(
            user=user,
            achievement_spec=code.achievement_spec,
        ).save()

        code.redeemed = True
        code.save()
        logging.info(
            "Responding with: %s" % build_text_response(code.achievement_spec.gain_message)
        )
        self.response.write(
            build_text_response(code.achievement_spec.gain_message)
        )


class NotifyHandler(webapp2.RequestHandler):
    def get(self):
        for unnotified in Achievement.all().filter("notified =", False):
            unnotified.notified = True
            unnotified.save()

            send_sms(
                unnotified.user.phone_number,
                unnotified.achievement_spec.gain_message
            )


class PhoneUserHandler(webapp2.RequestHandler):
    def post(self):
        PhoneUser(
            full_name=self.request.get("full_name"),
            phone_number=self.request.get("phone_number"),
            email=self.request.get("email")
        ).save()


def make_random_code():
    with open("nouns.txt", "r") as f:
        nouns = f.readlines()
    with open("describe.txt", "r") as f:
        adjectives = f.readlines()
    code = random.choice(adjectives).strip() + random.choice(nouns).strip()
    found = AchievementCode.all().filter("code =", code)

    if found.get():
        return make_random_code()
    else:
        return code


class CodeGenerationHandler(webapp2.RequestHandler):
    def post(self):
        num = int(self.request.get("num"))
        achievement_key = self.request.get("achievement_key")
        achievement_spec = AchievementSpec.all().filter(
            "achievement_key =", achievement_key
        )[0]

        for i in range(num):
            code = make_random_code()
            AchievementCode(
                code=code,
                achievement_spec=achievement_spec
            ).save()


def model_to_dict(model):
    SIMPLE_TYPES = (int, long, float, bool, dict, basestring, list)
    output = {}

    for key, prop in model.properties().iteritems():
        value = getattr(model, key)

        if value is None or isinstance(value, SIMPLE_TYPES):
            output[key] = value
        elif isinstance(value, datetime.date):
            # Convert date/datetime to ms-since-epoch ("new Date()").
            ms = time.mktime(value.utctimetuple())
            ms += getattr(value, 'microseconds', 0) / 1000
            output[key] = int(ms)
        elif isinstance(value, db.GeoPt):
            output[key] = {'lat': value.lat, 'lon': value.lon}
        elif isinstance(value, db.Model):
            output[key] = model_to_dict(value)
        else:
            raise ValueError('cannot encode ' + repr(prop))

    return output


class AchievementSpecHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write(json.dumps(
            [model_to_dict(i) for i in AchievementSpec.all()]
        ))

    def post(self):
        r = self.request.get
        AchievementSpec(
            achievement_key=r("achievement_key"),
            name=r("name"),
            description=r("description"),
            gain_message=r("gain_message"),
            point_value=int(r("point_value"))
        ).save()


class NotifyHandler(webapp2.RequestHandler):
    def get(self):
        unnotified = Achievement.all().filter('notified =', False)
        for achievement in unnotified:
            # POST to twilio
            pass
            achievement.notified = True
            achievement.save()


class RankingHandler(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Access-Control-Allow-Origin'] = '*'
        user_totals = []
        for user in PhoneUser.all():
            total = 0
            for a in Achievement.all().filter("user =", user):
                total += a.achievement_spec.point_value
            user_totals.append((total, user.full_name, user.email))
        user_totals.sort(reverse=True)
        callback = self.request.get("callback", None)
        self.response.write(make_jsonp(callback, json.dumps(
            [dict(name=i[1], score=i[0], email=i[2]) for i in user_totals]
        )))


def make_jsonp(callback, json):
    if not callback:
        return json
    return "%s(%s);" % (callback, json)


HARD_SECRET = "REDACTED"


def achiever_hash(user, granting_page):
    return hashlib.sha1(
        ":".join((str(user.key().id()), granting_page.code, HARD_SECRET))
    ).hexdigest()[:5]


class GrantingLinkHandler(webapp2.RequestHandler):
    def get(self):
        r = self.request.get
        user_id = r("user_id").strip()
        code = r("app_code").strip()
        user = PhoneUser.get_by_id(int(user_id))
        page = GrantingApp.all().filter("code =", code)[0]

        hashed = achiever_hash(user, page)

        url = page.url + "?" + "&".join((
            "user_id=%s" % user_id,
            "app_code=%s" % code,
            "hashed=%s" % hashed
        ))

        self.response.write(json.dumps(
            dict(url=url)
        ))


def granting_link(user, app):
    hashed = achiever_hash(user, app)

    url = app.url + "?" + "&".join((
        "user_id=%s" % user.key().id(),
        "app_code=%s" % app.code,
        "hashed=%s" % hashed
    ))
    return url


class GrantingAppHandler(webapp2.RequestHandler):
    def post(self):
        r = self.request.get
        spec_key = r("achievement_key").strip()
        spec = AchievementSpec.all().filter('achievement_key =', spec_key)[0]
        GrantingApp(
            achievement_spec=spec,
            url=r("app_url"),
            code=hashlib.sha1(str(random.random())).hexdigest()[:10]
        ).save()


class GrantingHandler(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Access-Control-Allow-Origin'] = '*'
        r = self.request.get
        user_id = r("user_id")
        user = PhoneUser.get_by_id(int(user_id))

        app_code = r("app_code")
        app = GrantingApp.all().filter("code =", app_code)[0]

        hashed = r("hashed")

        if achiever_hash(user, app) != hashed:
            self.error(401)
            return self.response.write("Sorry, the hash didn't work.")

        try:
            Achievement.all().filter('user =', user)[0]
        except IndexError:
            pass
        else:
            self.error(401)
            return self.response.write("Whoops, you've already got that achievement.")

        send_sms(user.phone_number, app.achievement_spec.gain_message)

        Achievement(
            user=user,
            achievement_spec=app.achievement_spec,
            notified=True
        ).save()

        return self.response.write("Achievement Granted.")


class BroadcastHandler(webapp2.RequestHandler):
    def post(self):
        message_template = self.request.get("message_template")
        app_code = self.request.get("app_code")
        app = None
        if app_code:
            app = GrantingApp.all().filter("code =", app_code)[0]

        for user in PhoneUser.all():
            link = ""
            if app:
                link = granting_link(user, app)
            message = message_template.replace("LINK", link)
            print "SENDING TO", user.phone_number
            send_sms(user.phone_number, message)


def build_text_response(out_text):
    return """
        <Response>
            <Sms>%s</Sms>
        </Response>
    """ % out_text


app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/redeem', RedeemHandler),
    ('/phone-user', PhoneUserHandler),
    ('/generate-codes', CodeGenerationHandler),
    ('/achievement-specs', AchievementSpecHandler),
    ('/notify-achievements', NotifyHandler),
    ('/rankings', RankingHandler),
    ('/generate-link', GrantingLinkHandler),
    ('/granting-app', GrantingAppHandler),
    ('/grant', GrantingHandler),
    ('/broadcast', BroadcastHandler),
    ('/notify', NotifyHandler),
], debug=True)
