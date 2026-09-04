// Sample exchanges that show a creator what each builder setting actually does to
// her messages. Templates, not generations: they redraw on every keystroke and
// slider drag, so they have to be instant and free.
//
// Each builder takes the live config and returns
//   { caption, turns: [{ who: 'fan' | 'her', text }], note }
// where caption and note are optional strings.
(function () {

  var FAN_DAY  = 'hey, how was your day?';
  var FAN_UPTO = 'what have you been up to?';

  var ARCHETYPES = {
    'Deadpan / Dry': 'survived it. barely. someone microwaved fish in the break room again so honestly my day peaked at 8am.',
    'Bubbly / Sweet': 'omg it was SO good!! 🥰 i got the last iced coffee before they ran out and i took it as a sign the universe likes me today',
    'Dominant / Edgy': 'long. i\'m in a mood. be nice to me and i might tell you about it.',
    'Girl-Next-Door': 'honestly pretty normal! work, groceries, burnt half the dinner. living the dream 😄',
    'Mysterious / Dark': 'quiet. i like the days nobody needs anything from me. you caught me at the good part.',
    'Playful / Teasing': 'better now that you\'ve shown up 😏 i was starting to think you\'d forgotten about me',
    'Intellectual / Witty': 'productive in the way that means i reorganised my whole bookshelf instead of doing the thing i was supposed to do.'
  };

  // Warmth 1-5, answering the same fan line. Cold -> affectionate.
  var WARMTH = [
    'fine.',
    'it was alright. long shift.',
    'pretty good actually! got home and ate way too much 😄',
    'long, but better now you\'re here 🙂 i\'ve been looking forward to this all day.',
    'so much better now that you\'ve messaged me 🥺 i kept checking my phone hoping you would.'
  ];

  var QUESTIONS = {
    'rarely': '',
    'sometimes': ' how about you?',
    'often': ' how was yours? tell me something good.',
    'very often': ' how was yours? did you eat? what are you doing tonight?'
  };

  var QUESTION_NOTE = {
    'rarely': 'She rarely asks back — she lets the fan lead. Fewer questions means quieter chats.',
    'sometimes': 'She asks back every few messages.',
    'often': 'She asks back most messages, which is what keeps a chat moving.',
    'very often': 'She asks back constantly. Great for engagement, but it can read as an interview.'
  };

  var PACE = {
    'slow': { at: 'Around message 30', desc: 'A slow burn. She stays warm and curious for a long time before anything flirty appears — fans stay for weeks.' },
    'moderate': { at: 'Around message 10', desc: 'Flirting shows up once there is real rapport. The default for a reason.' },
    'fast': { at: 'Around message 4', desc: 'She flirts almost straight away, once she knows a little about him.' },
    'instant': { at: 'Message 1', desc: 'Flirty from the very first reply. Converts fastest, burns out fastest.' }
  };

  var FLIRT_OFF = 'you\'re good company, you know that? this is the best part of my evening 🙂';
  var FLIRT_ON  = 'careful, i might start looking forward to these 😏 i\'m in your hoodie right now, for the record.';

  var CEILING = {
    'suggestive': 'Ceiling: suggestive. She flirts and hints, but never gets graphic.',
    'moderate': 'Ceiling: moderate. She goes noticeably further than this once rapport is built — direct about wanting him, without explicit detail.',
    'explicit': 'Ceiling: explicit. She goes fully explicit once rapport is built. The sample stays mild because early messages always do — she works up to the ceiling, she never opens at it.'
  };

  function first(list) {
    return String(list || '').split(',')[0].trim();
  }

  function pick(map, key, fallback) {
    return map[key] || map[fallback];
  }

  var builders = {

    archetype: function (cfg) {
      var key = cfg.archetype || 'Girl-Next-Door';
      var reply = ARCHETYPES[key] || ARCHETYPES['Girl-Next-Door'];
      return {
        caption: key,
        turns: [{ who: 'fan', text: FAN_DAY }, { who: 'her', text: reply }],
        note: 'The archetype sets her default attitude. Everything below adjusts it.'
      };
    },

    voice: function (cfg) {
      var w = Math.max(1, Math.min(5, parseInt(cfg.warmth, 10) || 3));
      var freq = cfg.question_freq || 'often';
      return {
        caption: 'Warmth ' + w + ' · asks back ' + freq,
        turns: [
          { who: 'fan', text: FAN_DAY },
          { who: 'her', text: WARMTH[w - 1] + pick(QUESTIONS, freq, 'often') }
        ],
        note: pick(QUESTION_NOTE, freq, 'often') +
              ' Your Speech Style Rules are applied on top of this — they control punctuation, emoji and slang.'
      };
    },

    interests: function (cfg) {
      var topic = first(cfg.interests);
      if (!topic) {
        return {
          turns: [],
          note: 'Add a topic above to see how she\'d work it into a conversation.'
        };
      }
      return {
        caption: 'Steering toward "' + topic + '"',
        turns: [
          { who: 'fan', text: FAN_UPTO },
          { who: 'her', text: 'honestly? ' + topic + '. i went down a rabbit hole with it last night and lost about three hours 😅 are you into any of that?' }
        ],
        note: 'She returns to her topics when a chat goes quiet, so it never dies on "nothing much".'
      };
    },

    flirt: function (cfg) {
      var pace = pick(PACE, cfg.flirt_pace, 'moderate');
      var on = !!cfg.nsfw_enabled;
      var level = cfg.nsfw_level || 'moderate';
      return {
        caption: pace.at,
        turns: [
          { who: 'fan', text: 'i\'ve been looking forward to talking to you all day' },
          { who: 'her', text: on ? FLIRT_ON : FLIRT_OFF }
        ],
        note: pace.desc + ' ' + (on ? pick(CEILING, level, 'moderate')
                                    : 'NSFW is off, so she stays warm and playful and never turns sexual.')
      };
    },

    funnel: function (cfg) {
      var label = (cfg.cta_label || '').trim() || 'come find me here';
      return {
        caption: 'Intrigue → Tease → Offer',
        turns: [
          { who: 'her', text: 'i shot something this afternoon that i probably shouldn\'t have 🙈' },
          { who: 'fan', text: 'now you have to tell me' },
          { who: 'her', text: 'i can\'t put it here, they\'d delete me. it\'s on my page — ' + label + ' and it\'s yours 💌' }
        ],
        note: 'She never opens with the offer. Rapport first, then a hint, then the ask — so it reads as a favour rather than a sale.'
      };
    }

  };

  window.PersonaExamples = builders;

})();
