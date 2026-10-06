"""The blog: its posts, and the HTML for the list and for one post.

Rendered on the server so a crawler reads the article in the first response.
When the posts were drawn in by JavaScript every post reached Google as the same
nine-word page with the same title, and none of them was indexed. Adding a post
means adding an entry here; the routes and the sitemap read this list."""
from datetime import datetime
from html import escape
from urllib.parse import quote

POSTS = [
    {
        'slug': 'from-fan-to-buyer',
        'category': 'Funnels',
        'date': 'ON JUL 13, 2025',
        'title': 'From Fan to Buyer: How a Persona Turns a Chat into a Sale',
        'excerpt': 'Discover how a well-defined persona moves a fan through rapport, intrigue and tease — and why the offer lands better when it never feels like selling.',
        'image': 'https://images.unsplash.com/photo-1522071820081-009f0129c71c?w=1200&q=70&auto=format&fit=crop',
        'body': '''
    <p class="lead">Every conversation that ends in a purchase followed the same
    shape. The persona learned something about the fan, referenced it later, and
    made the offer feel like a favour rather than a pitch.</p>

    <h2>Rapport comes before everything</h2>
    <p>The fastest way to lose a fan is to sell in the first three messages. A
    persona that opens with a question — and then remembers the answer — buys
    itself the right to make an offer later.</p>

    <blockquote>The offer is not a step. It is the natural consequence of the
    four messages before it.</blockquote>

    <h2>The five moments that matter</h2>
    <ul>
      <li><strong>Warm</strong> — a friendly opener that asks about the fan.</li>
      <li><strong>Engage</strong> — reference what they said, so they feel known.</li>
      <li><strong>Intrigue</strong> — hint that exclusive content exists.</li>
      <li><strong>Tease</strong> — a behind-the-scenes line implying there is more.</li>
      <li><strong>Offer</strong> — in character, in her voice, never a sales page.</li>
    </ul>

    <h2>Let the pace be a setting, not a guess</h2>
    <p>Some audiences want the slow burn; some are ready in a single evening.
    The persona builder makes escalation a dial you set once, so the funnel runs
    at the pace your fans actually respond to.</p>
''',
    },
    {
        'slug': 'writing-a-voice',
        'category': 'Personas',
        'date': 'ON MAY 4, 2025',
        'title': 'Writing a Voice: The Art of a Persona That Sounds Human',
        'excerpt': 'Explore what separates a persona fans talk to for hours from one they stop replying to after two messages.',
        'image': 'https://images.unsplash.com/photo-1499750310159-5b5d31d3a8e2?w=1200&q=70&auto=format&fit=crop',
        'body': '''
    <p class="lead">A voice is made of small, consistent choices: sentence
    length, punctuation, how often she asks something back.</p>
    <h2>Specific beats generic</h2>
    <p>"I work nights at a bar in Rotterdam" earns more replies than "I'm fun and
    outgoing". Details give the fan something to ask about.</p>
    <h2>Consistency is the whole trick</h2>
    <p>Fans forgive almost anything except a persona who changes personality
    between messages. Set the archetype, the warmth and the speech style once,
    and let every reply inherit them.</p>
''',
    },
    {
        'slug': 'phases-that-convert',
        'category': 'Funnels',
        'date': 'ON MAY 4, 2025',
        'title': 'Phases That Convert: Why Timing Beats Volume',
        'excerpt': 'Learn why the number of messages you send matters far less than which message arrives at which moment.',
        'image': 'https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?w=1200&q=70&auto=format&fit=crop',
        'body': '''
    <p class="lead">Sending more does not convert more. Sending the right thing
    at the right phase does.</p>
    <h2>Photos early, offers late</h2>
    <p>A high photo rate in phase one builds the sense that something is being
    shared. Save the CTA for the final phase, when it reads as a reward.</p>
    <h2>Measure by phase, not by day</h2>
    <p>Fans move at different speeds. Counting exchanges rather than calendar
    days keeps the funnel honest.</p>
''',
    },
    {
        'slug': 'photos-that-earn',
        'category': 'Content',
        'date': 'ON JUN 17, 2025',
        'title': 'Photos That Earn: Building a Set Fans Actually Unlock',
        'excerpt': 'Unpack how outfit locking, tagging and per-fan pricing turn a photo library into predictable revenue.',
        'image': 'https://images.unsplash.com/photo-1471897488648-5eae4ac6686b?w=1200&q=70&auto=format&fit=crop',
        'body': '''
    <p class="lead">A photo library only earns when the right image reaches the
    right fan at the right price.</p>
    <h2>Lock the outfit, keep the illusion</h2>
    <p>Nothing breaks immersion faster than three outfits in one conversation.
    Outfit locking keeps a session visually coherent.</p>
    <h2>Ladders, not flat prices</h2>
    <p>Start low, prove the value, and let each unlock make the next one easier
    to say yes to.</p>
''',
    },
]

_BY_SLUG = {p['slug']: p for p in POSTS}
_GRADIENT = 'linear-gradient(135deg,#ff5c38,#ff2d78 45%,#7c3aed)'
_ARROW = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
          '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')


def get(slug):
    return _BY_SLUG.get(slug)


def iso_date(p):
    return datetime.strptime(p['date'].removeprefix('ON '), '%b %d, %Y').strftime('%Y-%m-%d')


def _href(p):
    return '/blog/' + quote(p['slug'], safe='')


def _meta(p):
    return ('<div class="meta"><span class="pill">%s</span><span class="date">%s</span></div>'
            % (escape(p['category']), escape(p['date'])))


def _bg(p):
    return 'background-image:url(&quot;%s&quot;),%s' % (escape(p['image']), _GRADIENT)


def _card(p, heading='h3'):
    return ('<a class="card" href="%s">%s<%s>%s</%s><p>%s</p></a>'
            % (_href(p), _meta(p), heading, escape(p['title']), heading, escape(p['excerpt'])))


def list_html():
    if not POSTS:
        return ''
    lead, rest = POSTS[0], POSTS[1:]
    html = ('<a class="featured" href="%s"><div class="featured-img" style="%s"></div>'
            '<div class="featured-body">%s<h2>%s</h2><p>%s</p>'
            '<div class="read"><span>Read the full article %s</span></div></div></a>'
            % (_href(lead), _bg(lead), _meta(lead), escape(lead['title']),
               escape(lead['excerpt']), _ARROW))
    if rest:
        html += '<div class="grid">' + ''.join(_card(p) for p in rest) + '</div>'
    return html


def post_html(p):
    rest = [x for x in POSTS if x is not p][:3]
    html = ('<div class="post-head">%s<h1>%s</h1></div><div class="hero" style="%s"></div>'
            '<article>%s</article>' % (_meta(p), escape(p['title']), _bg(p), p['body']))
    if rest:
        html += ('<section class="more"><h2>More articles</h2><div class="grid">'
                 + ''.join(_card(x) for x in rest) + '</div></section>')
    return html
