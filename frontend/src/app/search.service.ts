import {
    inject,
    Injectable
} from '@angular/core';

import {
    HttpClient
} from '@angular/common/http';

import {
    Observable
} from 'rxjs';

import {
    EventCard,
    EventDetail,
    EventToRate,
    RatingResult,
    SearchRequest,
    SearchStreamEvent,
    SourceInfo
} from './search.model';

import {
    parseSseEvent,
    splitSseBuffer
} from './sse';


// # Configuration (le proxy Angular envoie /api vers FastAPI)

const API = '/api/v1';

const SEARCH_STREAM_URL = `${API}/recherche/stream`;

const UNDERSTAND_URL = `${API}/recherche/comprendre`;


// # Service

@Injectable({
    providedIn: 'root'
})
export class SearchService {

    private readonly http = inject(
        HttpClient
    );


    // # Lectures simples

    /** Détail d'un événement ; `sessionId` renvoie aussi l'avis de ce visiteur. */
    getEvent(
        eventId: string,
        sessionId = ''
    ): Observable<EventDetail> {
        const visitor = sessionId ? `?visiteur=${encodeURIComponent(sessionId)}` : '';
        return this.http.get<EventDetail>(
            `${API}/evenements/${encodeURIComponent(eventId)}${visitor}`
        );
    }


    getPartnerHighlights(
        limit = 3
    ): Observable<EventCard[]> {
        return this.http.get<EventCard[]>(
            `${API}/partenaires/a-ne-pas-manquer`,
            { params: { limit } }
        );
    }


    // # Avis des visiteurs

    rateEvent(
        eventId: string,
        sessionId: string,
        stars: number,
        comment: string
    ): Observable<RatingResult> {
        return this.http.post<RatingResult>(
            `${API}/evenements/${encodeURIComponent(eventId)}/avis`,
            { session_id: sessionId, stars, comment }
        );
    }


    /** Pouce haut (1) ou bas (-1) sur une réponse de SortirIA. */
    sendAnswerFeedback(
        requestId: number,
        rating: 1 | -1,
        comment = ''
    ): Observable<{ status: string }> {
        return this.http.post<{ status: string }>(
            `${API}/reponses/${requestId}/avis`,
            { rating, comment }
        );
    }


    getEventsToRate(
        sessionId: string,
        eventIds: string[]
    ): Observable<EventToRate[]> {
        return this.http.post<EventToRate[]>(
            `${API}/avis/a-donner`,
            { session_id: sessionId, event_ids: eventIds }
        );
    }


    getSources(): Observable<SourceInfo[]> {
        return this.http.get<SourceInfo[]>(
            `${API}/sources`
        );
    }


    // # Recherche diffusée étape par étape

    searchStream(
        request: SearchRequest
    ): Observable<SearchStreamEvent> {
        return new Observable<SearchStreamEvent>(
            subscriber => {
                const controller =
                    new AbortController();

                void this.readStream(
                    request,
                    subscriber,
                    controller.signal
                );

                return () => {
                    controller.abort();
                };
            }
        );
    }


    private async readStream(
        request: SearchRequest,
        subscriber: {
            next: (event: SearchStreamEvent) => void;
            error: (error: unknown) => void;
            complete: () => void;
        },
        signal: AbortSignal
    ): Promise<void> {
        // Étape « comprendre » tout de suite (30/10/2026) : Static Web Apps renvoie
        // le flux d'un bloc à la fin. Cet appel, sans base ni Mistral, répond en
        // quelques millisecondes ; il n'écrit plus rien dès que le flux a parlé.
        let streamStarted = false;
        void this.understandFirst(request, subscriber, signal, () => streamStarted);

        try {
            const response = await fetch(
                SEARCH_STREAM_URL,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        Accept: 'text/event-stream'
                    },
                    body: JSON.stringify(request),
                    signal
                }
            );

            // Limite de débit (16/10/2026) : le message de l'API est montré tel quel.
            if (response.status === 429) {
                const body = await response.json().catch(() => null) as { detail?: string } | null;
                subscriber.next({
                    event: 'error',
                    message: body?.detail ?? 'Trop de recherches d’affilée : réessayez dans un instant.'
                } as SearchStreamEvent);
                subscriber.complete();
                return;
            }

            if (!response.ok) {
                throw new Error(
                    `Erreur HTTP ${response.status}`
                );
            }

            if (response.body === null) {
                throw new Error(
                    'Le serveur n’a retourné aucun flux.'
                );
            }

            const reader = response.body
                .pipeThrough(new TextDecoderStream())
                .getReader();

            let buffer = '';

            while (true) {
                const result = await reader.read();

                if (result.done) {
                    break;
                }

                streamStarted = true;

                const { chunks, rest } = splitSseBuffer(
                    buffer + result.value
                );

                buffer = rest;

                for (const chunk of chunks) {
                    const event = parseSseEvent(chunk);

                    if (event !== null) {
                        subscriber.next(event);
                    }
                }
            }

            subscriber.complete();

        } catch (error) {
            if (
                error instanceof DOMException
                && error.name === 'AbortError'
            ) {
                return;
            }

            subscriber.error(error);
        }
    }


    private async understandFirst(
        request: SearchRequest,
        subscriber: { next: (event: SearchStreamEvent) => void },
        signal: AbortSignal,
        streamStarted: () => boolean
    ): Promise<void> {
        try {
            const response = await fetch(UNDERSTAND_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request),
                signal
            });

            if (!response.ok || streamStarted()) {
                return;
            }

            const body = await response.json() as { message?: string };

            if (streamStarted()) {
                return;
            }

            subscriber.next({
                event: 'progress', step: 'comprendre', status: 'completed', message: body.message ?? ''
            });
            subscriber.next({
                event: 'progress', step: 'rechercher', status: 'running', message: ''
            });
        } catch {
            // Sans cette étape anticipée, la recherche s'affiche comme avant.
        }
    }
}
