import {
    Component,
    DestroyRef,
    HostListener,
    OnInit,
    computed,
    inject,
    signal
} from '@angular/core';

import {
    Subscription
} from 'rxjs';

import {
    EventCard,
    EventDetail,
    EventToRate,
    Period,
    Public,
    SearchResult,
    SearchStep,
    SearchStreamEvent,
    SourceInfo,
    StepStatus,
    Zone
} from './search.model';

import {
    SearchService
} from './search.service';


// # Constantes

interface Choice<T> {
    value: T;
    label: string;
}

interface StepView {
    step: SearchStep;
    title: string;
    status: StepStatus;
    message: string;
}

/** Morceau de la réponse : du texte, ou un renvoi [n] vers une carte. */
/** Échange précédent de la conversation, gardé à l'écran (17/10/2026). */
interface PastExchange {
    question: string;
    answer: string;
    events: { event_id: string; ref: number | null; title: string; date: string }[];
}

interface AnswerPart {
    text: string;
    ref: number | null;
}

// « Tout public » (18/10/2026, retour des premiers testeurs) : aucun filtre de
// public, comme « Les deux » pour la zone ; pas « seulement les événements
// marqués tout public ».
const PUBLICS: Choice<Public | null>[] = [
    { value: null, label: 'Tout public' },
    { value: 'famille', label: 'Famille' },
    { value: 'soiree', label: 'Soirée' },
    { value: 'ados', label: 'Ados' },
    { value: 'seniors', label: 'Seniors' }
];

const ZONES: Choice<Zone | null>[] = [
    { value: 'montpellier', label: 'Montpellier' },
    { value: 'thau', label: 'Thau' },
    { value: null, label: 'Les deux' }
];

const PERIODS: Choice<Period>[] = [
    { value: 'aujourdhui', label: 'Aujourd’hui' },
    { value: 'weekend', label: 'Ce week-end' },
    { value: '7jours', label: '7 jours' },
    { value: '3mois', label: '3 mois' }
];

const STEP_TITLES: Record<SearchStep, string> = {
    comprendre: 'Compréhension de la demande',
    rechercher: 'Recherche dans la base',
    filtrer: 'Filtres',
    rediger: 'Rédaction de la réponse'
};

const STEPS: SearchStep[] = ['comprendre', 'rechercher', 'filtrer', 'rediger'];

// Affichées le temps que l'API réponde (mêmes noms que /api/v1/sources).
const DEFAULT_SOURCES = ['DATAtourisme', 'Programmes publiés par les collectivités', 'OpenAgenda', 'Partenaires SortirIA'];

const WELCOME_IDEAS = [
    'Un truc à faire avec les enfants samedi près de Sète',
    'Un concert ce soir à Montpellier',
    'Une sortie gratuite ce week-end'
];

/** Délai avant de refermer la fenêtre d'étapes une fois la réponse prête. */
const CLOSE_DELAY_MS = 700;

/** Navigateur : identifiant anonyme du visiteur et sorties consultées. */
const SESSION_KEY = 'sortiria_session';
const SEEN_KEY = 'sortiria_sorties_vues';
const MAX_SEEN = 30;

/** État du commentaire du visiteur, rappelé sous son avis. */
const OWN_COMMENT_STATUS: Record<string, string> = {
    aucun: '',
    publie: 'Commentaire publié.',
    en_attente: 'Commentaire en relecture : il sera publié après la modération (au plus tard la nuit prochaine).',
    refuse: 'Commentaire non publié (refusé par la modération) ; la note compte.'
};

const COMMENT_MESSAGES: Record<string, string> = {
    aucun: 'Merci, votre note est enregistrée.',
    publie: 'Merci, votre avis est publié.',
    en_attente: 'Merci, votre commentaire sera relu avant d’être publié.',
    refuse: 'Merci pour la note. Le commentaire n’a pas été publié (refusé par la modération).'
};


// # Stockage du navigateur (peut être indisponible : navigation privée)

function readStorage(key: string): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function writeStorage(key: string, value: string): void {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Sans stockage, l'avis reste possible pendant la visite.
    }
}

function newSessionId(): string {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
        return crypto.randomUUID();
    }

    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}


// # Composant

@Component({
    selector: 'app-root',
    standalone: true,
    templateUrl: './app.component.html',
    styleUrl: './app.component.scss'
})
export class AppComponent implements OnInit {

    private readonly searchService = inject(SearchService);

    private readonly destroyRef = inject(DestroyRef);

    private searchSubscription: Subscription | null = null;

    private closeTimer: ReturnType<typeof setTimeout> | null = null;


    // # Choix proposés

    readonly publics = PUBLICS;

    readonly zones = ZONES;

    readonly periods = PERIODS;

    readonly welcomeIdeas = WELCOME_IDEAS;


    // # État

    readonly question = signal('');

    // Aucun filtre imposé (16/10/2026) : la question décide. « Famille » et
    // « Ce week-end » cochés par défaut faisaient répondre « rien pour un
    // public familial » à « un concert ce soir à Montpellier ».
    readonly selectedPublic = signal<Public | null>(null);

    readonly selectedZone = signal<Zone | null>(null);

    readonly selectedPeriod = signal<Period>('3mois');

    readonly freeOnly = signal(false);

    /** Date précise lue dans la question (« sam. 18 oct. »), affichée dans « Quand ». */
    readonly questionDate = signal<string | null>(null);

    /** Échanges précédents, du plus ancien au plus récent (les 5 derniers). */
    readonly history = signal<PastExchange[]>([]);

    /** Téléphone : filtres repliés en une ligne après une recherche (16/10/2026),
     *  pour que la réponse soit visible sans faire défiler. */
    readonly filtersCollapsed = signal(isPhone());

    /** Cases cochées par la question (18/10/2026), et non par le visiteur : elles
     *  ne valent que pour cette question. La suivante repart sans elles ; une
     *  relance les retrouve par le contexte. */
    private readonly publicFromQuestion = signal(false);
    private readonly zoneFromQuestion = signal(false);

    /** « À ne pas manquer » a répondu (vide ou non) : avant, « Chargement… ». */
    readonly highlightsLoaded = signal(false);

    readonly sources = signal<string[]>(DEFAULT_SOURCES);

    readonly highlights = signal<EventCard[]>([]);

    readonly loading = signal(false);

    readonly showSteps = signal(false);

    readonly steps = signal<StepView[]>(this.emptySteps());

    readonly askedQuestion = signal('');

    readonly result = signal<SearchResult | null>(null);

    readonly error = signal<string | null>(null);

    readonly selectedId = signal<string | null>(null);

    readonly detail = signal<EventDetail | null>(null);

    readonly detailLoading = signal(false);

    readonly detailError = signal<string | null>(null);


    // # Avis

    /** Identifiant anonyme du visiteur : jamais stocké en clair côté serveur. */
    private readonly sessionId = this.loadSessionId();

    private seenIds: string[] = this.loadSeenIds();

    readonly toRate = signal<EventToRate[]>([]);

    readonly ratingStars = signal(0);

    readonly ratingHover = signal(0);

    readonly ratingComment = signal('');

    readonly ratingSending = signal(false);

    readonly ratingMessage = signal<string | null>(null);

    readonly ratingError = signal<string | null>(null);

    // Pouce haut / pouce bas sur la réponse (16/10/2026).
    readonly answerVote = signal<1 | -1 | null>(null);
    readonly answerComment = signal('');
    readonly answerFeedbackState = signal<'idle' | 'sending' | 'sent' | 'error'>('idle');
    /** Le visiteur a déjà noté et veut changer son avis. */
    readonly editingRating = signal(false);

    readonly starChoices = [1, 2, 3, 4, 5];


    // # Valeurs calculées

    /** Résumé des filtres, affiché quand ils sont repliés. */
    readonly filtersSummary = computed(() => {
        const parts = [
            PUBLICS.find(choice => choice.value === this.selectedPublic())?.label ?? 'Tout public',
            ZONES.find(choice => choice.value === this.selectedZone())?.label ?? 'Les deux',
            this.questionDate() ?? PERIODS.find(choice => choice.value === this.selectedPeriod())?.label ?? ''
        ];
        if (this.freeOnly()) {
            parts.push('Gratuit');
        }
        return parts.filter(Boolean).join(' · ');
    });

    /** « À ne pas manquer », à droite (18/10/2026) : sans les offres déjà à
     *  l'écran (cartes, encadré des offres proches) ; vide, il disparaît. */
    readonly sideHighlights = computed(() => {
        const answer = this.result();
        const shown = new Set([
            ...(answer?.events ?? []).map(card => card.event_id),
            ...(answer?.partner_offers ?? []).map(card => card.event_id)
        ]);
        return this.highlights().filter(item => !shown.has(item.event_id)).slice(0, 4);
    });

    /** Des offres de démonstration sont affichées : le bandeau le dit. */
    readonly showsDemo = computed(() => this.highlights().some(item => item.demo)
        || !!this.result()?.partner_offers?.some(item => item.demo)
        || !!this.result()?.events.some(item => item.demo));

    readonly progressPercent = computed(() => {
        const done = this.steps().filter(step => step.status === 'completed').length;

        return Math.round((done / STEPS.length) * 100);
    });

    readonly answerParts = computed<AnswerPart[]>(() => {
        const answer = this.result()?.answer ?? '';

        return answer
            .split(/(\[\d+\])/)
            .filter(part => part !== '')
            .map(part => {
                const match = /^\[(\d+)\]$/.exec(part);

                return match
                    ? { text: part, ref: Number(match[1]) }
                    : { text: part, ref: null };
            });
    });


    // # Démarrage

    ngOnInit(): void {
        this.searchService.getSources().subscribe({
            next: sources => {
                if (sources.length > 0) {
                    this.sources.set(sources.map(source => source.name));
                }
            },
            error: () => undefined
        });

        this.refreshToRate();

        this.loadHighlights();

        this.destroyRef.onDestroy(() => {
            this.searchSubscription?.unsubscribe();
            this.clearCloseTimer();
        });
    }


    // # Filtres

    togglePublic(value: Public | null): void {
        this.publicFromQuestion.set(false);
        this.selectedPublic.update(current => value === null || current === value ? null : value);
    }

    selectZone(value: Zone | null): void {
        this.zoneFromQuestion.set(false);
        this.selectedZone.set(value);
    }

    selectPeriod(value: Period): void {
        // Choisir une période remplace la date lue dans la question.
        this.questionDate.set(null);
        this.selectedPeriod.set(value);
    }

    toggleFree(): void {
        this.freeOnly.update(value => !value);
    }

    onQuestionInput(event: Event): void {
        this.question.set((event.target as HTMLTextAreaElement).value);
    }

    onQuestionKeydown(event: KeyboardEvent): void {
        // Entrée lance la recherche, Maj + Entrée passe à la ligne.
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            this.search();
        }
    }


    // # Recherche

    search(question = this.question(), followUp = false): void {
        const text = question.trim();

        if (this.loading()) {
            return;
        }

        // Fil de la conversation : la demande comprise précédente accompagne
        // la nouvelle question (« et pour les enfants ? »).
        const context = this.result()?.context ?? '';
        // Relance cliquée (17/10/2026) : la recherche affichée est complétée
        // (« … près de Sète, plutôt en matinée ? ») au lieu d'être remplacée.
        const isFollowUp = followUp && !!context && !!this.askedQuestion();
        const shown = isFollowUp ? completeQuestion(this.askedQuestion(), text) : text;
        // La réponse précédente reste affichée au-dessus de la nouvelle (17/10/2026).
        const previous = this.result();
        if (previous && this.askedQuestion()) {
            this.history.update(past => [...past, {
                question: this.askedQuestion(),
                answer: previous.answer,
                events: previous.events.slice(0, 4).map(card => ({
                    event_id: card.event_id, ref: card.ref, title: card.title, date: card.date
                }))
            }].slice(-5));
        }
        // Une case cochée par la question précédente ne vaut pas pour celle-ci
        // (18/10/2026 : « avec les enfants » puis « un concert ce soir »
        // cherchait encore pour les familles). Une relance la retrouve par le
        // contexte, et l'interface la recoche.
        if (this.publicFromQuestion()) {
            this.selectedPublic.set(null);
            this.publicFromQuestion.set(false);
        }
        if (this.zoneFromQuestion()) {
            this.selectedZone.set(null);
            this.zoneFromQuestion.set(false);
        }
        this.question.set(shown);
        this.askedQuestion.set(shown);
        this.error.set(null);
        this.steps.set(this.emptySteps());
        this.showSteps.set(true);
        this.loading.set(true);
        this.clearCloseTimer();
        this.searchSubscription?.unsubscribe();

        if (isPhone()) {
            this.filtersCollapsed.set(true);
        }

        this.searchSubscription = this.searchService.searchStream({
            question: text,
            public: this.selectedPublic(),
            zone: this.selectedZone(),
            period: this.selectedPeriod(),
            free_only: this.freeOnly(),
            context,
            follow_up: isFollowUp
        }).subscribe({
            next: event => this.onStreamEvent(event),
            error: () => this.fail('Le serveur ne répond pas. Vérifiez que l’API SortirIA est lancée.'),
            complete: () => this.loading.set(false)
        });
    }

    askSuggestion(suggestion: string): void {
        // Avant la première réponse (idées d'accueil), c'est une nouvelle demande.
        this.search(suggestion, !!this.result());
    }

    showFilters(): void {
        this.filtersCollapsed.set(false);
    }

    newConversation(): void {
        this.searchSubscription?.unsubscribe();
        this.filtersCollapsed.set(isPhone());
        this.loading.set(false);
        this.question.set('');
        this.askedQuestion.set('');
        this.result.set(null);
        this.history.set([]);
        this.questionDate.set(null);
        if (this.publicFromQuestion()) {
            this.selectedPublic.set(null);
            this.publicFromQuestion.set(false);
        }
        if (this.zoneFromQuestion()) {
            this.selectedZone.set(null);
            this.zoneFromQuestion.set(false);
        }
        this.error.set(null);
        this.selectedId.set(null);
        this.detail.set(null);
    }

    private onStreamEvent(event: SearchStreamEvent): void {
        if (event.event === 'progress') {
            this.steps.update(steps => steps.map(step => step.step === event.step
                ? { ...step, status: event.status, message: event.message || step.message }
                : step));
            return;
        }

        if (event.event === 'error') {
            this.fail(event.message);
            return;
        }

        this.result.set(event.result);
        this.loading.set(false);
        this.resetAnswerFeedback();

        // La question l'emporte sur les cases (17/10/2026) : « avec les enfants »
        // coche « Famille », « à Sète » coche « Thau ».
        const checked = event.result.checked;
        if (checked?.public && checked.public !== this.selectedPublic()) {
            this.selectedPublic.set(checked.public);
            this.publicFromQuestion.set(true);
        }
        if (checked?.zone && checked.zone !== this.selectedZone()) {
            this.selectedZone.set(checked.zone);
            this.zoneFromQuestion.set(true);
        }
        this.questionDate.set(checked?.date ?? null);

        // L'API n'était peut-être pas prête au chargement de la page : on réessaie.
        if (this.highlights().length === 0) {
            this.loadHighlights();
        }

        const first = event.result.events[0];

        if (first !== undefined) {
            this.selectEvent(first.event_id);
        } else {
            this.selectedId.set(null);
            this.detail.set(null);
        }

        this.closeTimer = setTimeout(() => this.showSteps.set(false), CLOSE_DELAY_MS);
    }

    private fail(message: string): void {
        this.loading.set(false);
        this.error.set(message);
        this.showSteps.set(false);
    }


    // # Fenêtre des étapes

    openSteps(): void {
        this.clearCloseTimer();
        this.showSteps.set(true);
    }

    closeSteps(): void {
        this.clearCloseTimer();
        this.showSteps.set(false);
    }

    @HostListener('document:keydown.escape')
    onEscape(): void {
        if (this.showSteps()) {
            this.closeSteps();
        }
    }

    private clearCloseTimer(): void {
        if (this.closeTimer !== null) {
            clearTimeout(this.closeTimer);
            this.closeTimer = null;
        }
    }

    private emptySteps(): StepView[] {
        return STEPS.map(step => ({
            step,
            title: STEP_TITLES[step],
            status: 'pending' as StepStatus,
            message: ''
        }));
    }


    // # Détail d'un événement

    /** Événement touché par le visiteur : sur téléphone, le détail est sous
     *  les résultats, on y descend directement (17/10/2026). La sélection
     *  automatique de la première carte, elle, ne fait pas défiler. */
    openEvent(eventId: string): void {
        this.selectEvent(eventId);
        if (isPhone()) {
            scrollToElement(document.getElementById('detail'));
        }
    }

    /** Téléphone : remonte à la carte touchée, ou au début de la réponse. */
    backToResults(): void {
        const id = this.selectedId();
        const card = id === null ? null
            : document.querySelector(`[data-event-id="${CSS.escape(id)}"]`);
        scrollToElement(card ?? document.querySelector('.thread'));
    }

    selectEvent(eventId: string): void {
        this.selectedId.set(eventId);
        this.detailLoading.set(true);
        this.detailError.set(null);
        this.resetRatingForm();
        this.rememberSeen(eventId);

        this.searchService.getEvent(eventId, this.sessionId).subscribe({
            next: detail => {
                if (this.selectedId() === eventId) {
                    this.detail.set(detail);
                    this.detailLoading.set(false);
                }
            },
            error: () => {
                if (this.selectedId() === eventId) {
                    this.detail.set(null);
                    this.detailLoading.set(false);
                    this.detailError.set('Cet événement n’est plus proposé (passé depuis plus de 7 jours, ou annulé).');
                }
            }
        });
    }

    /** « À ne pas manquer » : les offres partenaires mises en avant. */
    private loadHighlights(): void {
        this.searchService.getPartnerHighlights(6).subscribe({
            next: highlights => {
                this.highlights.set(highlights);
                this.highlightsLoaded.set(true);
            },
            error: () => {
                this.highlights.set([]);
                this.highlightsLoaded.set(true);
            }
        });
    }

    // # Avis : étoiles et commentaire

    setStars(stars: number): void {
        this.ratingStars.set(stars);
        this.ratingError.set(null);
    }

    onCommentInput(event: Event): void {
        this.ratingComment.set((event.target as HTMLTextAreaElement).value);
    }

    sendRating(): void {
        const event = this.detail();

        if (event === null || this.ratingStars() === 0 || this.ratingSending()) {
            if (this.ratingStars() === 0) {
                this.ratingError.set('Choisissez d’abord une note de 1 à 5 étoiles.');
            }
            return;
        }

        this.ratingSending.set(true);
        this.ratingError.set(null);

        this.searchService.rateEvent(event.event_id, this.sessionId, this.ratingStars(),
            this.ratingComment().trim()).subscribe({
            next: result => {
                this.ratingSending.set(false);
                this.ratingMessage.set(COMMENT_MESSAGES[result.comment_status] ?? COMMENT_MESSAGES['aucun']);
                this.toRate.update(items => items.filter(item => item.event_id !== event.event_id));
                this.editingRating.set(false);
                this.refreshDetail(event.event_id);
            },
            error: (error: { status?: number }) => {
                this.ratingSending.set(false);
                this.ratingError.set(error.status === 409
                    ? 'L’avis est possible du début de l’événement jusqu’à 7 jours après sa fin.'
                    : 'L’avis n’a pas pu être envoyé. Réessayez dans un instant.');
            }
        });
    }

    /** Pouce haut : envoyé tout de suite. Pouce bas : le visiteur peut dire
     *  ce qui manquait avant d'envoyer (facultatif). */
    voteAnswer(vote: 1 | -1): void {
        this.answerVote.set(vote);
        if (vote === 1) {
            this.sendAnswerFeedback();
        } else {
            this.answerFeedbackState.set('idle');
        }
    }

    onAnswerCommentInput(event: Event): void {
        this.answerComment.set((event.target as HTMLTextAreaElement).value);
    }

    sendAnswerFeedback(): void {
        const requestId = this.result()?.request_id;
        const vote = this.answerVote();
        if (requestId == null || vote === null || this.answerFeedbackState() === 'sending') {
            return;
        }
        this.answerFeedbackState.set('sending');
        this.searchService.sendAnswerFeedback(requestId, vote, this.answerComment().trim()).subscribe({
            next: () => this.answerFeedbackState.set('sent'),
            error: () => this.answerFeedbackState.set('error')
        });
    }

    private resetAnswerFeedback(): void {
        this.answerVote.set(null);
        this.answerComment.set('');
        this.answerFeedbackState.set('idle');
    }

    /** Recharge le détail après un avis : le visiteur voit tout de suite son
     *  avis, le nombre d'avis du lieu et, s'il est publié, son commentaire. */
    private refreshDetail(eventId: string): void {
        this.searchService.getEvent(eventId, this.sessionId).subscribe({
            next: detail => {
                if (this.selectedId() === eventId) {
                    this.detail.set(detail);
                }
            },
            error: () => undefined
        });
    }

    /** Remet le formulaire à la note déjà donnée, pour la modifier. */
    editRating(): void {
        const own = this.detail()?.my_rating;
        this.ratingMessage.set(null);
        this.ratingStars.set(own?.stars ?? 0);
        this.ratingComment.set(own?.comment ?? '');
        this.editingRating.set(true);
    }

    ownCommentStatus(status: string): string {
        return OWN_COMMENT_STATUS[status] ?? '';
    }

    private resetRatingForm(): void {
        this.editingRating.set(false);
        this.ratingStars.set(0);
        this.ratingHover.set(0);
        this.ratingComment.set('');
        this.ratingMessage.set(null);
        this.ratingError.set(null);
        this.ratingSending.set(false);
    }

    /** « Vos sorties à noter » : parmi les sorties consultées, celles notables maintenant. */
    private refreshToRate(): void {
        if (this.seenIds.length === 0) {
            this.toRate.set([]);
            return;
        }

        this.searchService.getEventsToRate(this.sessionId, this.seenIds).subscribe({
            next: items => this.toRate.set(items),
            error: () => this.toRate.set([])
        });
    }

    private rememberSeen(eventId: string): void {
        this.seenIds = [eventId, ...this.seenIds.filter(id => id !== eventId)].slice(0, MAX_SEEN);
        writeStorage(SEEN_KEY, JSON.stringify(this.seenIds));
    }

    private loadSeenIds(): string[] {
        try {
            const value: unknown = JSON.parse(readStorage(SEEN_KEY) ?? '[]');

            return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string').slice(0, MAX_SEEN) : [];
        } catch {
            return [];
        }
    }

    private loadSessionId(): string {
        const stored = readStorage(SESSION_KEY);

        if (stored !== null && stored.length >= 16) {
            return stored;
        }

        const created = newSessionId();
        writeStorage(SESSION_KEY, created);

        return created;
    }

    selectRef(ref: number): void {
        const card = this.result()?.events.find(event => event.ref === ref);

        if (card !== undefined) {
            this.openEvent(card.event_id);
        }
    }


    // # Affichage

    stars(rating: number | null): string {
        if (rating === null) {
            return '☆☆☆☆☆';
        }

        const full = Math.round(rating);

        return '★'.repeat(full) + '☆'.repeat(5 - full);
    }

    /** Liste des autres dates, affichée au survol de « + n dates ». */
    otherDatesTitle(card: EventCard): string {
        return 'Aussi le : ' + card.other_dates.map(other => other.date).join(', ');
    }

    ratingLabel(card: EventCard, given = 0): string {
        if (card.venue_rating === null) {
            // La moyenne d'un lieu ne s'affiche qu'à partir de 3 notes.
            return given > 0
                ? `${given} avis · moyenne affichée à partir de 3`
                : 'Pas encore assez d’avis';
        }

        return `${card.venue_rating.toLocaleString('fr-FR')} · ${card.venue_rating_count} avis`;
    }

    stepStatusLabel(status: StepStatus): string {
        return status === 'completed' ? 'Fait' : status === 'running' ? 'En cours…' : 'À venir';
    }
}


/** « Un truc avec les enfants près de Sète » + « Plutôt en matinée ? »
 *  → « Un truc avec les enfants près de Sète, plutôt en matinée ? ». */
/** Écran étroit (téléphone, mise en page sur une colonne). */
function isPhone(): boolean {
    return typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 900px)').matches;
}

/** Fait défiler jusqu'à l'élément (en douceur, sauf si le visiteur réduit les animations). */
function scrollToElement(element: Element | null): void {
    if (!element) {
        return;
    }
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    element.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
}

export function completeQuestion(previous: string, followUp: string): string {
    const base = previous.trim().replace(/[\s\u00a0]*[?!.…]+$/u, '');
    const suite = followUp.trim();
    if (!base) {
        return suite;
    }
    return `${base}, ${suite.charAt(0).toLocaleLowerCase('fr-FR')}${suite.slice(1)}`;
}
