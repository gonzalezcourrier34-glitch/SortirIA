// # Modèles échangés avec l'API SortirIA (app/interface_api.py, app/search_service.py)


export type Public = 'famille' | 'soiree' | 'ados' | 'seniors';

export type Zone = 'montpellier' | 'thau';

export type Period = 'aujourdhui' | 'weekend' | '7jours' | '3mois';


export interface SearchRequest {
    question: string;
    public: Public | null;
    zone: Zone | null;
    period: Period;
    free_only: boolean;
    /** Demande précédente (SearchResult.context) : une relance garde le fil. */
    context: string;
    /** Relance cliquée sous la réponse : toujours une suite de la demande précédente. */
    follow_up?: boolean;
}


/** Une autre date du même événement (même titre, même lieu). */
export interface OtherDate {
    event_id: string;
    date: string;
}


/** Un événement tel que l'API le renvoie (carte et détail). */
export interface EventCard {
    ref: number | null;
    event_id: string;
    title: string;
    date: string;
    start_at: string;
    place: string;
    category: string;
    /** Type tel qu'on l'écrit (« soirée », « théâtre ») : c'est lui qui s'affiche. */
    category_label: string;
    price: string;
    audiences: string[];
    summary: string;
    url: string;
    source: string;
    source_attribution: string;
    partner: boolean;
    partner_name: string;
    venue_rating: number | null;
    venue_rating_count: number;
    /** Autres dates du même événement : une seule carte, sur sa date la plus proche. */
    other_dates: OtherDate[];
}


export interface VenueComment {
    stars: number;
    text: string;
    date: string;
    /** L'événement noté (gardé avec l'avis, l'événement lui-même ayant pu être supprimé). */
    event_title: string;
    event_date: string;
}


export interface EventDetail extends EventCard {
    description: string;
    comments: VenueComment[];
    /** Avis possible maintenant : du début de l'événement à 7 jours après sa fin. */
    can_rate: boolean;
    /** Sinon, date à partir de laquelle il sera possible (« Sam. 17 oct. »). */
    rate_from: string;
    /** Notes données sur ce lieu, même sous le seuil de 3 où la moyenne s'affiche. */
    venue_ratings: number;
    /** L'avis de ce visiteur sur cet événement, s'il en a donné un. */
    my_rating: OwnRating | null;
}


/** L'avis que le visiteur a donné (il le revoit dans le détail). */
export interface OwnRating {
    stars: number;
    comment: string;
    comment_status: 'aucun' | 'publie' | 'refuse' | 'en_attente';
    date: string;
}


/** Réponse à l'envoi d'un avis. */
export interface RatingResult {
    status: string;
    /** aucun (pas de commentaire), publie, refuse, en_attente (modération indisponible). */
    comment_status: 'aucun' | 'publie' | 'refuse' | 'en_attente';
}


/** Une sortie consultée que le visiteur peut noter maintenant. */
export interface EventToRate {
    event_id: string;
    title: string;
    date: string;
    place: string;
}


export interface SearchResult {
    answer: string;
    events: EventCard[];
    suggestions: string[];
    filters: string;
    degraded: boolean;
    request_id: number | null;
    /** Demande complète comprise, à renvoyer avec la question suivante. */
    context: string;
    /** false : rien d'exact dans la base, les propositions sont des idées voisines. */
    exact: boolean;
    /** Rien d'exact : offres de nos partenaires les plus proches de la demande
     *  (absent d'une réponse mise en cache avant le 15/10 après-midi). */
    partner_offers?: EventCard[];
}


export interface SourceInfo {
    name: string;
    events: number;
}


// # Étapes de la recherche (même ordre que le backend)

export type SearchStep = 'comprendre' | 'rechercher' | 'filtrer' | 'rediger';

export type StepStatus = 'pending' | 'running' | 'completed';


export interface SearchProgressEvent {
    event: 'progress';
    step: SearchStep;
    status: StepStatus;
    message: string;
}


export interface SearchCompletedEvent {
    event: 'completed';
    result: SearchResult;
}


export interface SearchErrorEvent {
    event: 'error';
    message: string;
}


export type SearchStreamEvent =
    | SearchProgressEvent
    | SearchCompletedEvent
    | SearchErrorEvent;
