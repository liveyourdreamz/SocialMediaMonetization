"""What the screens show, computed in code from the store (D24: models never do the
arithmetic). Every figure on the Portfolio carries its source; a figure with no
source is not rendered (Proof, StatTile)."""

from __future__ import annotations

from cap.ui.views.card import (  # noqa: F401 - the screens read cap.ui.views.*
    QueueItem,
    card_context,
    queue_items,
)
from cap.ui.views.digest import (  # noqa: F401 - the screens read cap.ui.views.*
    Digest,
    digest,
)
from cap.ui.views.outbox import (  # noqa: F401 - the screens read cap.ui.views.*
    RELEASE_TEXT,
    outbox,
    package,
    parked,
    publish_results,
    published,
    recent_briefs,
    release_view,
)
from cap.ui.views.portfolio import (  # noqa: F401 - the screens read cap.ui.views.*
    CLOSED,
    ChannelCard,
    Figure,
    Portfolio,
    canary_rate,
    canary_record,
    portfolio,
)
from cap.ui.views.progress import (  # noqa: F401 - the screens read cap.ui.views.*
    STEPS,
    workflow,
)
from cap.ui.views.text import (  # noqa: F401 - the screens read cap.ui.views.*
    GATE_NAMES,
    LABELS,
    REASON_CHIPS,
    REASON_TEXT,
    about_minutes,
    channel_label,
    day,
    is_rtl,
    minutes,
    script_paragraphs,
    word_count,
)
