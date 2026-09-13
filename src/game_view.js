// Shared game framing. Blender's Export Stage writes this in Fixed camera mode.
// One camera for every stage; width/height are design pixels, fov is vertical.
export const GAME_VIEW = {
  width: 600, height: 800,
  at: [0, 3.91, 3.72], yaw: 0, pitch: 10, dist: 20, fov: 35,
};
