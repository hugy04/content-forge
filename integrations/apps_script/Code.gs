/**
 * content-forge → Google Drive sink.
 *
 * Deployed as a web app running as YOU, so it inherits your Drive quota and
 * needs no service account, no GCP project and no OAuth refresh token. The
 * pipeline POSTs one JSON body; this creates the Doc, saves the image, and
 * appends a row to the content log.
 *
 * Setup (about five minutes):
 *   1. script.google.com → New project → paste this file over Code.gs
 *   2. Project Settings → Script properties → add:
 *        SHARED_SECRET   any long random string
 *   3. Deploy → New deployment → type "Web app"
 *        Execute as:        Me
 *        Who has access:    Anyone
 *   4. Copy the /exec URL into GDRIVE_WEBAPP_URL in .env, and the same secret
 *      into GDRIVE_SHARED_SECRET.
 *
 * The folder and spreadsheet are created automatically on the first request and
 * their ids cached in Script Properties.
 */

var FOLDER_NAME = 'content-forge';
var SHEET_NAME = 'content-forge log';

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var props = PropertiesService.getScriptProperties();

    var expected = props.getProperty('SHARED_SECRET');
    if (!expected || body.secret !== expected) {
      return json({ ok: false, error: 'bad or missing secret' });
    }

    var folder = getOrCreateFolder_(props);
    var imageUrl = '';
    if (body.image_base64) {
      var blob = Utilities.newBlob(
        Utilities.base64Decode(body.image_base64), 'image/png',
        slug_(body.title) + '-' + String(body.run_id).slice(0, 8) + '.png');
      var file = folder.createFile(blob);
      file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      imageUrl = file.getUrl();
    }

    var docUrl = createDoc_(folder, body, imageUrl);
    var sheetUrl = appendLog_(props, body, docUrl, imageUrl);

    return json({ ok: true, doc_url: docUrl, image_url: imageUrl, sheet_url: sheetUrl });
  } catch (err) {
    return json({ ok: false, error: String(err) });
  }
}

function doGet() {
  return json({ ok: true, service: 'content-forge sink', hint: 'POST JSON here' });
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function slug_(s) {
  return String(s || 'untitled').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '').slice(0, 50) || 'untitled';
}

function getOrCreateFolder_(props) {
  var id = props.getProperty('FOLDER_ID');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (err) { /* recreate below */ }
  }
  var folders = DriveApp.getFoldersByName(FOLDER_NAME);
  var folder = folders.hasNext() ? folders.next() : DriveApp.createFolder(FOLDER_NAME);
  props.setProperty('FOLDER_ID', folder.getId());
  return folder;
}

/** Minimal markdown → Google Docs: ## headings, bullets, bold, paragraphs. */
function createDoc_(folder, body, imageUrl) {
  var doc = DocumentApp.create(body.title || 'Untitled');
  var docBody = doc.getBody();
  docBody.clear();

  docBody.appendParagraph(body.title || 'Untitled')
    .setHeading(DocumentApp.ParagraphHeading.TITLE);
  if (body.subtitle) {
    docBody.appendParagraph(body.subtitle)
      .setHeading(DocumentApp.ParagraphHeading.SUBTITLE);
  }

  if (body.image_base64) {
    try {
      var img = Utilities.newBlob(Utilities.base64Decode(body.image_base64), 'image/png');
      var inserted = docBody.appendImage(img);
      var width = 468;                       // fit the page margins
      var ratio = inserted.getHeight() / inserted.getWidth();
      inserted.setWidth(width).setHeight(Math.round(width * ratio));
      if (body.alt_text) {
        docBody.appendParagraph(body.alt_text).setItalic(true)
          .setHeading(DocumentApp.ParagraphHeading.NORMAL);
      }
    } catch (err) { /* an unreadable image must not lose the document */ }
  }

  var lines = String(body.body_markdown || '').split('\n');
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].replace(/\s+$/, '');
    if (!line) continue;
    if (line.indexOf('### ') === 0) {
      docBody.appendParagraph(clean_(line.slice(4)))
        .setHeading(DocumentApp.ParagraphHeading.HEADING3);
    } else if (line.indexOf('## ') === 0) {
      docBody.appendParagraph(clean_(line.slice(3)))
        .setHeading(DocumentApp.ParagraphHeading.HEADING2);
    } else if (line.indexOf('# ') === 0) {
      docBody.appendParagraph(clean_(line.slice(2)))
        .setHeading(DocumentApp.ParagraphHeading.HEADING1);
    } else if (/^[-*]\s+/.test(line)) {
      docBody.appendListItem(clean_(line.replace(/^[-*]\s+/, '')))
        .setGlyphType(DocumentApp.GlyphType.BULLET);
    } else if (/^\d+\.\s+/.test(line)) {
      docBody.appendListItem(clean_(line.replace(/^\d+\.\s+/, '')))
        .setGlyphType(DocumentApp.GlyphType.NUMBER);
    } else {
      docBody.appendParagraph(clean_(line));
    }
  }

  var file = DriveApp.getFileById(doc.getId());
  folder.addFile(file);
  DriveApp.getRootFolder().removeFile(file);
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  doc.saveAndClose();
  return doc.getUrl();
}

function clean_(s) {
  return s.replace(/\*\*(.+?)\*\*/g, '$1').replace(/`(.+?)`/g, '$1');
}

function appendLog_(props, body, docUrl, imageUrl) {
  var id = props.getProperty('SHEET_ID');
  var sheet;
  if (id) {
    try { sheet = SpreadsheetApp.openById(id); } catch (err) { sheet = null; }
  }
  if (!sheet) {
    sheet = SpreadsheetApp.create(SHEET_NAME);
    props.setProperty('SHEET_ID', sheet.getId());
    sheet.getActiveSheet().appendRow([
      'timestamp', 'run_id', 'topic', 'title', 'style', 'angle', 'audience',
      'art_direction', 'palette', 'words', 'revisions', 'qc_passed',
      'image_provider', 'status', 'seconds', 'cost_usd', 'doc_url', 'image_url']);
    sheet.getActiveSheet().setFrozenRows(1);
  }
  sheet.getActiveSheet().appendRow([
    new Date(), body.run_id, body.topic, body.title, body.style, body.angle,
    body.audience, body.art_direction, body.palette, body.word_count,
    body.revisions, body.qc_passed, body.image_provider, body.status,
    body.duration_seconds, body.cost_usd, docUrl, imageUrl]);
  return sheet.getUrl();
}
