#!/usr/bin/env python

from google.appengine.dist import use_library
use_library('django', '1.2')

from django.utils import simplejson as json

from google.appengine.api import taskqueue, urlfetch, users
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import template, util

import base64
import datetime
import functools
import logging
import os
import re
import urllib

LOCALHOST = "http://mass-text.appspot.com/twilio"

def makeTwilioHttpParts(path_tmpl, twilio_data):
  url = "https://api.twilio.com/2010-04-01/Accounts/"+(path_tmpl % twilio_data.SID)
  headers = {
    "Authorization": "Basic "+base64.b64encode(twilio_data.SID+":"+twilio_data.AUTH),
    "Content-Type": "application/x-www-form-urlencoded"
  }
  return (url, headers)
  
def parsePhone(num):
  num = "".join(re.split(r'[^0-9]', num))
  if len(num) < 10 or len(num) > 11:
    return None
  if num.startswith("1"):
    return "+"+num
  else:
    return "+1"+num

def micros(dt):
  if not dt:
    dt = datetime.datetime()
  ms = int(round(float(dt.strftime("%s")+"."+str(dt.microsecond)),3)*1000000)
  return ms

class TwilioCallbackHandler(webapp.RequestHandler):
  # A handler for twilio callbacks
  
  def getTwilioData(self, sid):
    return TwilioData.all().filter("SID =", sid).get()
  
  def post(self):
    if self.request.path == '/twilio/sms_status_cb':
      op = self.request.get("op")
      sid = self.request.get("SmsSid")
      status = self.request.get("SmsStatus")
      tr = TextResult.all().ancestor(db.Key(op)).filter("sid =", sid).get()
      if not tr:
        logging.warning("An SMS was updated that doesn't correspond to a TextResult; SID: " + str(sid) + "; op: " + str(op) + "; status: " + str(status))
        return
      tr.status = status
      tr.put()
    elif self.request.path == '/twilio/incoming_sms':
      td = self.getTwilioData(self.request.get("AccountSid"))
      self.response.headers['Content-Type'] = "text/plain"
      if td:
        self.response.out.write(td.TextAutoResponse)
    elif self.request.path == '/twilio/incoming_call':
      td = self.getTwilioData(self.request.get("AccountSid"))
      self.response.headers['Content-Type'] = "text/plain"
      if td:
        self.response.out.write(td.VoiceAutoResponse)
      
    
    

class TextingWorker(webapp.RequestHandler):
  # The TaskQueue task that actually makes the calls to twilio.

  def sendSingleMessage(self, textop, recipient, msg, twilio_data):
    rpc = urlfetch.create_rpc()
    (url, headers) = makeTwilioHttpParts("%s/SMS/Messages.json", twilio_data)
    params = {
      "From": twilio_data.From,
      "To": recipient,
      "Body": msg,
      "StatusCallback": LOCALHOST+"/sms_status_cb?op="+str(textop.key())
    }
    payload = urllib.urlencode(params)
    urlfetch.make_fetch_call(rpc, url, payload, "POST", headers, validate_certificate=True)
    rpc.TW_request_data = {
      "recipient": recipient
    }
    return rpc

  def processMessageResponse(self, rpc, textop, response_json=None):
    if response_json is None:
      response = {
        "recipient": rpc.TW_request_data["recipient"],
        "sid": "<unknown>",
        "status": "failed-rpc"
      }
    else:
      try:
        response = json.loads(response_json)
      except:
        response = {
          "recipient": rpc.TW_request_data["recipient"],
          "sid": "<unknown>",
          "status": "failed-data"
        }
    tr = rpc.TW_request_data["tr"]
    tr.status = response["status"]
    tr.sid = response["sid"]
    tr.put()

  def sendMessage(self, textop, recipients, msg, twilio_data):
    rpcs = []
    for r in recipients:
      tr = TextResult(textop)
      tr.recipient = r
      tr.status = "new"
      tr.put()
      rpc = self.sendSingleMessage(textop, r, msg, twilio_data)
      rpc.TW_request_data["tr"] = tr
      rpcs.append(rpc)
    for r in rpcs:
      try:
        response = r.get_result()
        self.processMessageResponse(r, textop, response.content)
      except:
        self.processMessageResponse(r, textop)

  def post(self):
    logging.info("running texting task")
    key = self.request.get('key')
    textop = TextOperation.get(db.Key(key))
    twilio_data = TwilioData.get_by_key_name(textop.user)
    self.sendMessage(textop, textop.recipientList, textop.message, twilio_data)
    


  
class TextOperation(db.Model):
  # A database object that organizes a texting attempt
  message = db.StringProperty()
  recipientList = db.StringListProperty()
  user = db.StringProperty()
  created = db.DateTimeProperty(auto_now_add=True)

class TextResult(db.Model):
  # Holds the results of a particular text attempt.
  # Parent of this object is the TextOperation
  recipient = db.StringProperty()
  sid = db.StringProperty()
  status = db.StringProperty()
  modified = db.DateTimeProperty(auto_now=True)

class TwilioData(db.Model):
  # per-user data. Key name is the user_id of the user.
  SID = db.StringProperty()
  AUTH = db.StringProperty()
  From = db.StringProperty()
  TextAutoResponse = db.StringProperty()
  VoiceAutoResponse = db.StringProperty()
  modified = db.DateTimeProperty(auto_now=True)



def requireGoogleAccount(func):
  @functools.wraps(func)
  def wrapper(*args, **kwds):
    self = args[0]
    current_user = users.get_current_user()
    if not current_user:
      self.renderTemplate('login_required.html', {"fwdurl": users.create_login_url(self.request.uri)})
      return
    return func(*args, **kwds)
  return wrapper


def requireLocalAccount(func):
  @requireGoogleAccount
  @functools.wraps(func)
  def wrapper(*args, **kwds):
    self = args[0]
    td = self.getTwilioData()
    if not td:
      self.redirect('/account')
      return
    return func(*args, **kwds)
  return wrapper


class RequestHandler(webapp.RequestHandler):
  def renderTemplate(self, filename, values={}):
    path = os.path.join(os.path.dirname(__file__), 'templates', filename)
    if users.get_current_user():
      values["username"] = users.get_current_user().nickname()
      values["logouturl"] = users.create_logout_url("/")
    self.response.out.write(template.render(path, values))  

  def getTwilioData(self, sid=None):
    if sid:
      return TwilioData.all().filter("SID =", sid).get()
    else:
      return TwilioData.get_by_key_name(users.get_current_user().user_id())



class AccountHandler(RequestHandler):
  # handles account creation, update

  def accountError(self, msg, data=None):
    # oops, there was a problem.
    self.renderTemplate('account_required.html', {
      "error": msg,
      "data": data
    })

  def verifyTwilioCredentials(self, data):
    def err(msg):
      self.accountError(msg, data)

    if len(data.SID) == 0 or len(data.AUTH) == 0 or \
       len(data.TextAutoResponse) == 0 or len (data.VoiceAutoResponse) == 0:
      err("Please enter all the requested information.")
      return
    (url, headers) = makeTwilioHttpParts("%s.json", data)
    # verify credentials with twilio
    try:
      res = urlfetch.fetch(url, headers=headers)
    except:
      err("An error occurred verifying your credentials. Please try again.")
      return
    if res.status_code != 200:
      err("Your credentials don't appear to be valid. Please try again.")
      return
    # verify account is active
    resDict = json.loads(res.content)
    if resDict["status"] != "active":
      err("Your account doesn't appear to be active. Please try again.")
      return
    return True
  
  def listValidFromNumbers(self, data):
    def err(msg):
      self.accountError(msg, data)
    # verify chosen phone number is listed
    (url, headers) = makeTwilioHttpParts("%s/IncomingPhoneNumbers.json", data)
    try:
      res = urlfetch.fetch(url, headers=headers)
    except:
      err("An error occurred verifying your credentials. Please try again.")
      return          
    if res.status_code != 200:
      err("Your credentials don't appear to be valid. Please try again.")
      return
    try:
      numbers = json.loads(res.content)["incoming_phone_numbers"]
    except:
      err("Unable to get a list of valid phone numbers. If this error continues, contact your administrator.")
      return
    return [(x["friendly_name"], x["sid"], x["phone_number"]) for x in numbers if x["capabilities"]["sms"]]
  
  def setTwilioHandlerUrls(self, data, numberSid):
    def err(msg):
      self.accountError(msg, data)
    (url, headers) = makeTwilioHttpParts("%s/IncomingPhoneNumbers/"+numberSid+".json", data)
    params = {
      "VoiceUrl": LOCALHOST+"/incoming_call",
      "VoiceMethod": "POST",
      "SmsUrl": LOCALHOST+"/incoming_sms",
      "SmsMethod": "POST"
    }
    payload = urllib.urlencode(params)
    try:
      res = urlfetch.fetch(url, payload, "POST", headers)
    except:
      err("Unable to set callback URLs for phone number. Please try again later.")
      return
    if res.status_code != 200:
      err("Unable to set callback URLs for phone number. Please try again later.")
      return
    return True
    
    

  @requireGoogleAccount
  def get(self):
    data = self.getTwilioData()
    numbers = None
    if data and data.SID and data.AUTH:
      numbers = self.listValidFromNumbers(data)
    self.renderTemplate('account_required.html', { "data": self.getTwilioData(), "numbers": numbers })
  
  @requireGoogleAccount
  def post(self):
    current_user = users.get_current_user()
    data = TwilioData(key_name=current_user.user_id())
    data.SID = self.request.get('SID')
    data.AUTH = self.request.get('AUTH')
    data.TextAutoResponse = self.request.get('TextAutoResponse')
    data.VoiceAutoResponse = self.request.get('VoiceAutoResponse')
    if self.request.get('From'):
      data.From = self.request.get('From')

    def err(msg):
      self.accountError(msg, data)
    # verify inputs
    if not self.verifyTwilioCredentials(data):
      return
      
    numbers = self.listValidFromNumbers(data)
    if numbers is None:
      return
    if len(numbers) == 0:
      err("Your account doesn't seem to have any SMS-enabled numbers. Please create one and try again.")
      return
    
    if data.From:
      matchingNumbers = [x[1] for x in numbers if x[2] == data.From]
      if len(matchingNumbers) == 1:
        if self.setTwilioHandlerUrls(data, matchingNumbers[0]):
          data.put()
          # woo, success!
          self.redirect('/')
        else:
          err("Failed to update Twilio handler URLs. Please try again later.")
      else:
        err("You specified a number that doesn't support SMS or isn't your Twilio number. Please try again.")
      return
    
    if len(numbers) == 1:
      data.From = numbers[0][2]
      if self.setTwilioHandlerUrls(data, numbers[0][1]):
        data.put()
        # woo, success!
        self.redirect('/')
      else:
        err("Failed to update Twilio handler URLs. Please try again later.")
    elif len(numbers) > 1 and not data.From:
      self.renderTemplate('account_required.html', { "data": data, "numbers": numbers, "error": "Please choose a phone number too."})
      

    
    
class WatchingHandler(RequestHandler):
  # Handles watching pages

  @requireLocalAccount
  def get(self):
    current_user = users.get_current_user()
    key = self.request.get("id")
    textop = TextOperation.get(db.Key(key))
    if self.request.path == '/watch':
      if current_user.user_id() != textop.user and not users.is_current_user_admin():
        self.redirect('/')
        return
      results = TextResult.all().ancestor(textop)
      try:
        lastModified = max([x.modified for x in results])
      except:
        lastModified = datetime.datetime.now()        
      self.renderTemplate('watch.html', {
        "op": textop,
        "results": [{"key": str(x.key()), "recipient": x.recipient, "status": x.status, "modified": micros(x.modified)}
                    for x in results],
        "lastModified": micros(lastModified)
      })
    elif self.request.path == '/watch.json':
      self.response.headers['Content-Type'] = "application/json"
      if current_user.user_id() != textop.user:
        self.response.out.write('{redirect:"/"}')
        return
      lastModified = datetime.datetime.fromtimestamp(float(self.request.get('lastModified'))/1000000)
      logging.info("scanning for changes since "+str(micros(lastModified)))
      results = [x for x in TextResult.all().ancestor(textop).filter('modified >', lastModified) if x.modified > lastModified]
      if len(results) > 0:
        lastModified = micros(max([x.modified for x in results]))
      else:
        lastModified = None
      self.response.out.write(json.dumps({
        "results": [{"key": str(x.key()), "recipient": x.recipient, "status": x.status, "modified": micros(x.modified)}
                    for x in results],
        "lastModified": lastModified
      }))
    else:
      self.redirect('/')
      

class MainHandler(RequestHandler):
  # Main web handler

  @requireLocalAccount
  def get(self):
    current_user_id = users.get_current_user().user_id()
    if self.request.get('user') and users.is_current_user_admin():
      current_user_id = self.request.get('user')
    td = self.getTwilioData()
    ops = TextOperation.all().filter("user =", current_user_id).order("-created")
    if not self.request.get("all"):
      ops = ops.fetch(10)
    self.renderTemplate('main.html', {
      "ops": ops,
      "data": td
    })
  
  @requireLocalAccount
  def post(self):
    current_user = users.get_current_user()
    td = TwilioData.get_by_key_name(current_user.user_id())
    if not td:
      self.redirect('/')
      return
    textop = TextOperation()
    textop.message = self.request.get('message')
    recipientText = self.request.get('recipients')
    def err(msg):
      ops = TextOperation.all().filter("user =", current_user.user_id()).order("-created")
      if not self.request.get("all"):
        ops = ops.fetch(10)
      self.renderTemplate('main.html', {
        "ops": ops,
        "data": td,
        "err": msg,
        "message": textop.message,
        "recipients": recipientText
      })
    recipients = ["".join(re.split('[^0-9]', x)) for x in re.split('[,\\n]+', recipientText)]
    recipients = [parsePhone(x) for x in recipients if (len(x) > 0)]
    if len(textop.message) == 0:
      return err("Please provide a message.")
    if len(textop.message) > 160:
      return err("Message is too long: SMS messages must be 160 characters or shorter.")
    if len([x for x in recipients if x is None]) > 0:
      return err("Invalid recipient in list! Please confirm your list and try again.")
    textop.recipientList = list(set(recipients))
    textop.user = current_user.user_id()
    textop.put()
    taskqueue.add(url='/tasks/textingworker', params={'key': str(textop.key())})
    self.redirect("/watch?id="+str(textop.key()))


def main():
  application = webapp.WSGIApplication([(r'/(?:dotext)?', MainHandler),
                                        (r'/account(?:/new)?', AccountHandler),
                                        (r'/watch(?:\.json)?', WatchingHandler),
                                        ('/tasks/textingworker', TextingWorker),
                                        ('/twilio/.*', TwilioCallbackHandler)],
                                       debug=True)
  util.run_wsgi_app(application)


if __name__ == '__main__':
  main()
