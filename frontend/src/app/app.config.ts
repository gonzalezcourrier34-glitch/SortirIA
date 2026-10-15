import { ApplicationConfig } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';


// # Configuration

export const appConfig: ApplicationConfig = {
    providers: [
        provideHttpClient()
    ]
};
