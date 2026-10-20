import { apiRequest } from "./api";

export type MusicSection = {
  name?: string;
  start: number;
  end: number;
  mood: string;
};

export type MusicSpecification = {
  genre: string;
  mood: string[];
  tempo: number;
  key: string;
  instruments: string[];
  sections: MusicSection[];
};

export type CreateSpecificationRequest = {
  prompt: string;
  genre?: string;
  mood?: string[];
  tempo?: number;
  key?: string;
  instruments?: string[];
  sections?: MusicSection[];
};

type SpecificationResponse = {
  specification: MusicSpecification;
};

export function createMusicSpecification(request: CreateSpecificationRequest): Promise<SpecificationResponse> {
  return apiRequest<SpecificationResponse>("/api/v1/specifications", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
