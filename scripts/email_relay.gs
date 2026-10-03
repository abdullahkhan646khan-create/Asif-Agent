/**
 * Email relay for the Post Agent (EMAIL_MODE=apps_script).
 * The app sends it the email over HTTPS and this script sends it from the Gmail account it lives in.
 *
 * Setup (5 minutes, logged in as the SENDER Gmail):
 *  1. Open https://script.google.com → New project → delete everything → paste this file.
 *  2. Change SECRET below to a long random text. Put the same text in .env as EMAIL_RELAY_SECRET.
 *  3. Select the function "testSend" at the top and press Run. Allow the permissions
 *     (Google shows "Google hasn't verified this app" → Advanced → Go to ... → Allow; it is your own script).
 *     You should receive a "Relay works" email.
 *  4. Deploy → New deployment → type "Web app" → Execute as: Me → Who has access: Anyone → Deploy.
 *  5. Copy the Web app URL (ends with /exec) into .env as EMAIL_RELAY_URL.
 *  If you edit this script later: Deploy → Manage deployments → edit → Version: New version.
 *
 * Limit: about 100 recipients per day on a normal Gmail account (plenty for review emails).
 */
const SECRET = 'CHANGE-ME-to-the-same-value-as-EMAIL_RELAY_SECRET';

function doPost(e) {
  let out;
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.secret !== SECRET) throw new Error('wrong secret');
    const options = { htmlBody: req.html, name: req.from_name || 'Post Agent' };
    const inline = req.inline || {};
    if (Object.keys(inline).length) {
      options.inlineImages = {};
      Object.keys(inline).forEach(function (key) {
        options.inlineImages[key] = Utilities.newBlob(Utilities.base64Decode(inline[key]), 'image/jpeg', key + '.jpg');
      });
    }
    MailApp.sendEmail(req.to, req.subject, req.text || '', options);
    out = { ok: true, remaining_today: MailApp.getRemainingDailyQuota() };
  } catch (err) {
    out = { ok: false, error: String(err) };
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function testSend() {
  MailApp.sendEmail(Session.getActiveUser().getEmail(), 'Relay works', 'The Post Agent email relay is ready.');
}
