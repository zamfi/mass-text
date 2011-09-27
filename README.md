mass-text: an App Engine-hosted Twilio frontend for sending large quantities of a single message to several recipients.
=========

This app was whipped up in a weekend; it's not pretty, it's not robust, it's not anything but barely functional.

Functionality
-------------

To set up your own app:
1. Create a [Google App Engine](http://appengine.google.com) app.
2. Update `app.yaml` to point to your new app id.
3. Update the `LOCALHOST` parameter in `main.py` to point to your app. (Like I said, it's not pretty.)
4. Deploy and enjoy.

To use:
1. Create a [Twilio](http://twilio.com) account.
2. Visit the main page of your newly-deployed app.
3. Follow the (admittedly spare) instructions.

This app requires a dedicated Twilio phone number for use. Incoming texts and phone calls are responded to automatically with a message you set when creating your account on this app.

Caveats
-------

1. Can only send a message to 5000 recipients at a time.
2. There are probably bugs. Woo!

Updates
-------

If you make changes to this app, let me know!