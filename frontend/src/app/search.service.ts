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
}
