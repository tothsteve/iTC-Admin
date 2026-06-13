/**
 * ITC-Admin Invoice Tagger — Gmail add-on.
 *
 * Thin UI layer: when a message is opened, shows two buttons (Vállalati / Pénztár).
 * Clicking applies an ITC/Process-* label to the thread. The local Python workflow
 * (`integrated_workflow.py --mode labels`) polls for these labels, processes the
 * invoice (Dropbox + Google Sheets) and removes the label. This add-on never touches
 * Dropbox or Sheets — it only labels mail.
 */

// type key -> Gmail label name (must match settings.label_triggers / learn_triggers)
var LABELS_ = {
  'Vallalati': 'ITC/Process-Vallalati',
  'Penztar': 'ITC/Process-Penztar',
  'LearnVallalati': 'ITC/Learn-Vallalati',
  'LearnPenztar': 'ITC/Learn-Penztar'
};

// type key -> human-readable label for the card
var LABEL_DISPLAY_ = {
  'Vallalati': 'Vállalati (Bejövő)',
  'Penztar': 'Pénztár',
  'LearnVallalati': 'Tanítás → Vállalati',
  'LearnPenztar': 'Tanítás → Pénztár'
};

/** Contextual trigger: build the card when a message is opened. */
function onGmailMessageOpen(e) {
  return buildCard_(e);
}

/** Build the add-on card based on the message's current ITC/Process-* state. */
function buildCard_(e) {
  GmailApp.setCurrentMessageAccessToken(e.gmail.accessToken);
  var message = GmailApp.getMessageById(e.gmail.messageId);
  var thread = message.getThread();
  var current = currentTriggerType_(thread);

  var section = CardService.newCardSection();

  if (current) {
    section.addWidget(CardService.newTextParagraph()
      .setText('Sorba téve: <b>' + LABEL_DISPLAY_[current] + '</b> ✓'));
    section.addWidget(makeButton_('Kivesz a sorból', 'onDequeue', e.gmail.messageId, null));
  } else {
    section.addWidget(CardService.newTextParagraph()
      .setText('Feldolgozás ismert partnerként:'));
    section.addWidget(makeButton_('Vállalati (Bejövő)', 'onQueue', e.gmail.messageId, 'Vallalati'));
    section.addWidget(makeButton_('Pénztár', 'onQueue', e.gmail.messageId, 'Penztar'));
    section.addWidget(CardService.newTextParagraph()
      .setText('Új partner tanítása (interaktív, terminál):'));
    section.addWidget(makeButton_('Tanítás → Vállalati', 'onQueue', e.gmail.messageId, 'LearnVallalati'));
    section.addWidget(makeButton_('Tanítás → Pénztár', 'onQueue', e.gmail.messageId, 'LearnPenztar'));
  }

  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('ITC-Admin'))
    .addSection(section)
    .build();
}

/** Create a text button bound to an action with the given parameters. */
function makeButton_(text, functionName, messageId, type) {
  var params = { 'messageId': messageId };
  if (type) {
    params['type'] = type;
  }
  var action = CardService.newAction()
    .setFunctionName(functionName)
    .setParameters(params);
  return CardService.newTextButton()
    .setText(text)
    .setOnClickAction(action);
}

/** Action: queue this message under the chosen type (mutually exclusive). */
function onQueue(e) {
  GmailApp.setCurrentMessageAccessToken(e.gmail.accessToken);
  var type = e.parameters.type;
  var message = GmailApp.getMessageById(e.parameters.messageId);
  var thread = message.getThread();

  // Remove the other trigger label(s), then add the chosen one.
  for (var key in LABELS_) {
    removeLabel_(thread, LABELS_[key]);
  }
  getOrCreateLabel_(LABELS_[type]).addToThread(thread);

  return refresh_(e, 'Sorba téve: ' + LABEL_DISPLAY_[type]);
}

/** Action: remove this message from the processing queue. */
function onDequeue(e) {
  GmailApp.setCurrentMessageAccessToken(e.gmail.accessToken);
  var message = GmailApp.getMessageById(e.parameters.messageId);
  var thread = message.getThread();
  for (var key in LABELS_) {
    removeLabel_(thread, LABELS_[key]);
  }
  return refresh_(e, 'Kivéve a sorból');
}

/** Which trigger type (if any) is currently applied to the thread. */
function currentTriggerType_(thread) {
  var names = thread.getLabels().map(function (l) { return l.getName(); });
  for (var key in LABELS_) {
    if (names.indexOf(LABELS_[key]) !== -1) {
      return key;
    }
  }
  return null;
}

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function removeLabel_(thread, name) {
  var label = GmailApp.getUserLabelByName(name);
  if (label) {
    label.removeFromThread(thread);
  }
}

/** Rebuild the card and show a transient notification. */
function refresh_(e, notificationText) {
  var nav = CardService.newNavigation().updateCard(buildCard_(e));
  return CardService.newActionResponseBuilder()
    .setNavigation(nav)
    .setNotification(CardService.newNotification().setText(notificationText))
    .build();
}
