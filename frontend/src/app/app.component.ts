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
interface AnswerPart {
    text: string;
    ref: number | null;
}

const PUBLICS: Choice<Public>[] = [
    { value: 'famille', label: 'Famille' },
    { value: 'soiree', label: 'Soirée' },
    { value: 'ados', label: 'Ados' },
    { value: 'seniors', label: 'Seniors' }
];

const ZONES: Choice<Zone | null>[] = [
    { value: 'montpellier', label: 'Montpellier' },
    { value: 'thau', label: 'Thau' },
    { value: null, label: 'Partout' }
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

const DEFAULT_SOURCES = ['OpenAgenda', 'DATAtourisme', 'Agglopôle Sète', 'Partenaires'];

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

    readonly selectedPublic = signal<Public | null>('famille');

    readonly selectedZone = signal<Zone | null>(null);

    readonly selectedPeriod = signal<Period>('weekend');

    readonly freeOnly = signal(false);

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

    readonly starChoices = [1, 2, 3, 4, 5];


    // # Valeurs calculées

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

    togglePublic(value: Public): void {
        this.selectedPublic.update(current => current === value ? null : value);
    }

    selectZone(value: Zone | null): void {
        this.selectedZone.set(value);
    }

    selectPeriod(value: Period): void {
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

    search(question = this.question()): void {
        const text = question.trim();

        if (this.loading()) {
            return;
        }

        // Fil de la conversation : la demande comprise précédente accompagne
        // la nouvelle question (« et pour les enfants ? »).
        const context = this.result()?.context ?? '';
        this.question.set(text);
        this.askedQuestion.set(text);
        this.error.set(null);
        this.steps.set(this.emptySteps());
        this.showSteps.set(true);
        this.loading.set(true);
        this.clearCloseTimer();
        this.searchSubscription?.unsubscribe();

        this.searchSubscription = this.searchService.searchStream({
            question: text,
            public: this.selectedPublic(),
            zone: this.selectedZone(),
            period: this.selectedPeriod(),
            free_only: this.freeOnly(),
            context
        }).subscribe({
            next: event => this.onStreamEvent(event),
            error: () => this.fail('Le serveur ne répond pas. Vérifiez que l’API SortirIA est lancée.'),
            complete: () => this.loading.set(false)
        });
    }

    askSuggestion(suggestion: string): void {
        this.search(suggestion);
    }

    newConversation(): void {
        this.searchSubscription?.unsubscribe();
        this.loading.set(false);
        this.question.set('');
        this.askedQuestion.set('');
        this.result.set(null);
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

    selectEvent(eventId: string): void {
        this.selectedId.set(eventId);
        this.detailLoading.set(true);
        this.detailError.set(null);
        this.resetRatingForm();
        this.rememberSeen(eventId);

        this.searchService.getEvent(eventId).subscribe({
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
            next: highlights => this.highlights.set(highlights),
            error: () => this.highlights.set([])
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
            },
            error: (error: { status?: number }) => {
                this.ratingSending.set(false);
                this.ratingError.set(error.status === 409
                    ? 'L’avis est possible du début de l’événement jusqu’à 7 jours après sa fin.'
                    : 'L’avis n’a pas pu être envoyé. Réessayez dans un instant.');
            }
        });
    }

    private resetRatingForm(): void {
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
            this.selectEvent(card.event_id);
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

    ratingLabel(card: EventCard): string {
        if (card.venue_rating === null) {
            return 'Pas encore assez d’avis';
        }

        return `${card.venue_rating.toLocaleString('fr-FR')} · ${card.venue_rating_count} avis`;
    }

    stepStatusLabel(status: StepStatus): string {
        return status === 'completed' ? 'Fait' : status === 'running' ? 'En cours…' : 'À venir';
    }
}
