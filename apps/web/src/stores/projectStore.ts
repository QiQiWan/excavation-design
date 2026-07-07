import { create } from 'zustand';
import type { Project } from '../types/domain';

interface ProjectState {
  selectedProject?: Project;
  setSelectedProject: (project?: Project) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  selectedProject: undefined,
  setSelectedProject: (project) => set({ selectedProject: project })
}));
