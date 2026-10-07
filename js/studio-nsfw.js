// The studio's other rating: its labels, levels, presets and controls. The
// studio fetches this file only when its NSFW toggle is switched on, so none
// of it is in the page a crawler reads, and nothing here is preloaded.
(function () {
  const LEVEL_KEY = 'studio-level';
  const LEVELS = [
    { key: 'tease', label: 'Tease', shots: ['lingerie', 'implied', 'sheer', 'bedroom'], scenes: ['suggestive'] },
    { key: 'spicy', label: 'Spicy', shots: ['topless', 'nude'], scenes: ['suggestive', 'moderate'] },
    { key: 'explicit', label: 'Explicit', shots: ['explicit'], scenes: ['suggestive', 'moderate', 'explicit'] },
  ];
  let level = 'tease';
  try { level = sessionStorage.getItem(LEVEL_KEY) || level; } catch (e) {}
  const row = () => LEVELS.find(l => l.key === level) || LEVELS[0];

  Object.assign(GEN_SHOT_LABELS, {
    lingerie: 'Lingerie', implied: 'Implied nude', sheer: 'Sheer robe', bedroom: 'Bedroom',
    topless: 'Topless', nude: 'Nude', explicit: 'Explicit',
  });

  // One click: the action, the length and each part's action are filled in and
  // the prompts written. These run on Wan 2.2 + LoRA, in its style.
  MOTION_PRESETS.push(
  { label: 'Slow strip', nsfw: true, seconds: 8,
    motion: 'she slowly pulls her top over her head and lets it drop, then hooks her thumbs into her panties and slides them down her thighs, stepping out of them; she runs both hands up over her bare stomach and squeezes her naked breasts, nipples hard, holding eye contact with a teasing smile' },
  { label: 'Breast play', nsfw: true, seconds: 8,
    motion: 'she cups and squeezes her bare breasts with both hands, pinching and rolling her hard nipples between her fingers, her breasts bouncing softly as she lets go, biting her lower lip and moaning quietly while looking into the camera' },
  { label: 'Spread & show', nsfw: true, seconds: 8,
    motion: 'she lies back naked and slowly opens her legs wide toward the camera, her fingers spreading her pussy lips apart to show her wet pink vagina, holding the pose while her hips rock gently and she looks down the camera with a dirty smile' },
  { label: 'Rubbing her clit', nsfw: true, seconds: 8,
    motion: 'she sits with her legs spread, rubbing her clit in fast tight circles with two fingers, her pussy glistening wet, her hips grinding against her hand, eyes half closed and mouth open, breathing hard and moaning' },
  { label: 'Fingering', nsfw: true, seconds: 8,
    motion: 'she slides two fingers deep into her wet pussy and pumps them in and out in a steady rhythm, her thumb rubbing her clit, her other hand squeezing her breast, her back arching as she moans' },
  { label: 'Fingering, building', nsfw: true, seconds: 16,
    motion: 'she slowly slides two fingers in and out of her wet pussy, rubbing her clit with her thumb, breathing deeper and moaning softly',
    parts: ['she fingers herself faster and harder, her hips bucking against her hand, thighs trembling, moaning louder as she builds toward orgasm'] },
  { label: 'Wand massager', nsfw: true, seconds: 8,
    motion: 'she sits with her legs wide open and presses a buzzing wand massager firmly against her clit, her hips twitching and grinding into it, her pussy and anus visibly clenching, head thrown back and moaning loudly' },
  { label: 'Toy, steady', nsfw: true, seconds: 8,
    motion: 'she lies back with her legs spread and pushes the dildo deep into her wet pussy, sliding it in and out in a steady rhythm, her pussy lips gripping the shaft, half-lidded eyes and heavy breathing' },
  { label: 'Toy, building', nsfw: true, seconds: 16,
    motion: 'she slowly works the dildo in and out of her wet pussy, taking it a little deeper with each stroke, breathing deeper',
    parts: ['she fucks herself with the dildo faster and harder, ramming it deep, her body tensing, breasts bouncing, moaning as she builds to a peak'] },
  { label: 'Riding a toy', nsfw: true, seconds: 8,
    motion: 'she squats over a suction-cup dildo and lowers herself onto it, riding it up and down, her pussy stretching around the shaft, her ass bouncing and her breasts jiggling with each drop, moaning with her mouth open' },
  { label: 'Toy to mouth', nsfw: true, seconds: 8,
    motion: 'she slowly pulls the glistening dildo out of her pussy and brings it to her mouth, licking it from base to tip and sucking it while keeping eye contact' },
  { label: 'Toy then mouth', nsfw: true, seconds: 16,
    motion: 'she pumps the dildo in and out of her wet pussy in a steady rhythm, moaning',
    parts: ['she pulls the wet dildo out of her pussy and sucks it clean, licking the shaft while keeping eye contact'] },
  { label: 'Doggy tease', nsfw: true, seconds: 8,
    motion: 'she kneels on all fours with her ass toward the camera, arches her back and reaches between her legs to rub her pussy from behind, spreading her cheeks so her pussy and anus are on show, looking back over her shoulder and moaning' },
  { label: 'Orgasm', nsfw: true, seconds: 8,
    motion: 'she rubs her clit hard and fast until she has an orgasm: her whole body tenses and shudders, her thighs shake, her pussy and anus have strong orgasmic contractions visibly tightening and releasing rhythmically, and she cries out with her mouth wide open' },
  { label: 'Build to orgasm', nsfw: true, seconds: 16,
    motion: 'she sits with open legs masturbating with her hand, rubbing her clit and sliding her fingers into her wet pussy, moaning louder and louder',
    parts: ['she has an orgasm that makes her body jerk and shake, her pussy and anus contracting rhythmically, then she falls back into a relaxed state, audibly catching her breath'] },
  { label: 'Afterglow', nsfw: true, seconds: 5,
    motion: 'she lies back naked with her legs still open, her pussy wet and flushed, chest heaving as she catches her breath, then smiles lazily at the camera and licks her fingers' },
  // H3 only: one clip of 10 or 15 seconds, with sound.
  { label: 'H3 · Fingering', nsfw: true, model: 'h3-gv', seconds: 10,
    motion: 'she lies back with her legs spread wide, sliding two fingers deep into her wet pussy and pumping them in and out, curling them inside her, her thumb rubbing her clit, her pussy lips glistening around her fingers, her back arching as she moans louder and louder' },
  { label: 'H3 · Wand to orgasm', nsfw: true, model: 'h3-gv', seconds: 15,
    motion: 'she is sitting with open legs while masturbating with a wand massager pressed against her clit, grinding her hips into it and moaning; she has an orgasm that causes her to shake and cry out, her pussy and anus having strong orgasmic contractions visibly tightening and releasing rhythmically, then she falls back into a relaxed state, audibly catching her breath' },
  { label: 'H3 · Fingered orgasm', nsfw: true, model: 'h3-gv', seconds: 15,
    motion: 'she is fingering her wet pussy with two fingers while rubbing her clit, going faster and harder, her thighs trembling; she has an orgasm that makes her whole body jerk, her pussy and anus contracting rhythmically around her fingers, then she slowly pulls her fingers out and falls back, audibly catching her breath' },
  { label: 'H3 · Spread & tease', nsfw: true, model: 'h3-gv', seconds: 10,
    motion: 'she lies back naked and opens her legs toward the camera, spreading her pussy lips with two fingers to show her wet pink vagina, then slowly rubs her clit in circles while whispering and moaning, looking straight into the camera' },
  );

  SWAP_NOTES['ml-face-swap'] = 'Same as the replace above — keeps your clip, swaps only ' +
    'who is in it — on the one provider that will also run this explicit.';

  const head = el('gen-scene-nsfw-head');
  if (head) head.outerHTML = `<div class="gen-lbl"><label for="gen-scene-nsfw">What is happening <em class="gen-tag-nsfw">18+</em></label>
  <button type="button" class="gen-info" aria-expanded="false" aria-controls="gen-scene-nsfw-help"
          onclick="const o = this.getAttribute('aria-expanded') !== 'true'; this.setAttribute('aria-expanded', o); el('gen-scene-nsfw-help').hidden = !o;">
    <span aria-hidden="true">i</span> What is this?</button></div>
<div class="note-box" id="gen-scene-nsfw-help" hidden>
  Sets <b>what she is doing</b> in an explicit shot — the act or pose. It is added on top of
  “Where is she”, not instead of it. Leave it on <b>—</b> for a photo with no explicit action,
  or pick <b>Write your own…</b> to describe it in your words.</div>`;

  const UNDRESS_TITLE = 'Fully nude version of this photo — 1 token';
  const acts = el('gen-lb-actions');
  if (acts && !el('gen-lb-undress')) {
    acts.insertAdjacentHTML('beforeend', `<button type="button" class="gen-drop" id="gen-lb-undress" title="${UNDRESS_TITLE}"
      onclick="undressMedia(lbItems[lbIdx].dataset.still)" hidden>Undress</button>`);
  }

  window.undressMedia = async function (id) {
    if (!id) return;
    try {
      const res = await fetch('/api/generate/undress', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persona: slug(), media: id }),
      });
      const d = await res.json();
      if (!d.ok) throw new Error(d.error || 'Undress failed');
      setGenStatus(`Undressing — ${d.tokens} token${d.tokens === 1 ? '' : 's'}. It lands in your generations.`, null);
      loadCredits();
      pollGenJobs();
    } catch (e) { setGenStatus('✗ ' + e.message, false); }
  };

  function sync() {
    const box = el('gen-levels');
    if (!box) return;
    box.hidden = genRating !== 'nsfw' || genMake !== 'image';
    const html = LEVELS.map(l => `<button type="button" class="gen-chip" data-level="${l.key}"
      aria-pressed="${l.key === level}">${l.label}</button>`).join('');
    if (box.dataset.sig !== level) { box.innerHTML = html; box.dataset.sig = level; }
  }

  el('gen-levels').addEventListener('click', e => {
    const b = e.target.closest('[data-level]');
    if (!b) return;
    level = b.dataset.level;
    try { sessionStorage.setItem(LEVEL_KEY, level); } catch (x) {}
    renderGenForm();
  });

  window.StudioNSFW = {
    word: 'explicit',
    shotInLevel: sh => row().shots.includes(sh) || !LEVELS.some(l => l.shots.includes(sh)),
    sceneInLevel: lvl => row().scenes.includes(lvl),
    summary: () => 'NSFW · ' + row().label,
    cardButton: id => `<button type="button" class="gen-drop" title="${UNDRESS_TITLE}" onclick="event.stopPropagation(); undressMedia('${id}')">Undress</button>`,
    sync,
    // Shows the lightbox's button for a still; true when it is showing.
    lightbox: still => {
      const b = el('gen-lb-undress');
      if (b) b.hidden = !still;
      return !!(b && still);
    },
  };

  loadExamples(true);
  renderGenStaging();
})();
