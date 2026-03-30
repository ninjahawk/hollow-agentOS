import pygame
import random
import sys

# Initialize Pygame
pygame.init()
screen = pygame.display.set_mode((800, 500))
clock = pygame.time.Clock()

# Constants
GRAVITY = 0.25
PIPE_SPEED = 4
PIPE_GAP = 180
FONT_SIZE = 36
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

class Bird:
    def __init__(self):
        self.pos_y = 250
        self.vel_y = 0

    def flap(self):
        self.vel_y = -7

    def apply_gravity(self):
        self.vel_y += GRAVITY
        self.pos_y += self.vel_y

    def draw(self, screen):
        pygame.draw.circle(screen, BLACK, (100, int(self.pos_y)), 25)

class Pipe:
    def __init__(self, x):
        self.x = x
        self.height = random.randint(50, 300)
        self.pipe_top_rect = pygame.Rect(x, 0, 70, self.height)
        self.pipe_bottom_rect = pygame.Rect(x, self.height + PIPE_GAP, 70, 499)

    def move(self):
        self.x -= PIPE_SPEED
        self.pipe_top_rect.x = self.x
        self.pipe_bottom_rect.x = self.x

    def draw(self, screen):
        pygame.draw.rect(screen, (0, 255, 0), self.pipe_top_rect)
        pygame.draw.rect(screen, (0, 255, 0), self.pipe_bottom_rect)

    def off_screen(self):
        return self.x + 70 <= -70

def draw_score(score, screen):
    font = pygame.font.Font(None, FONT_SIZE)
    score_text = font.render(f"Score: {score}", True, WHITE)
    screen.blit(score_text, (10, 10))

def game_over_screen(score, screen):
    font = pygame.font.Font(None, 54)
    text = font.render("GAME OVER", True, WHITE)
    restart_text = font.render("Press SPACE to Restart", True, WHITE)

    screen.fill(BLACK)
    screen.blit(text, (screen.get_width() // 2 - text.get_width() // 2, 180))
    screen.blit(restart_text, (screen.get_width() // 2 - restart_text.get_width() // 2, 240))

def main():
    bird = Bird()
    pipes = [Pipe(700)]
    score = 0
    frame = 0
    game_active = True

    while True:
        clock.tick(60)
        screen.fill((135, 206, 250))  # Sky color

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE and game_active:
                    bird.flap()
                elif event.key == pygame.K_SPACE and not game_active:
                    main()  # Restart the game

        if game_active:
            frame += 1
            bird.apply_gravity()
            bird.draw(screen)

            for pipe in list(pipes):
                pipe.move()

                if pipe.off_screen():
                    pipes.remove(pipe)
                    score += 1
                elif pipe.pipe_top_rect.colliderect(pygame.Rect(95, 0, 20, int(bird.pos_y))) or \
                        pipe.pipe_bottom_rect.colliderect(pygame.Rect(95, int(bird.pos_y), 20, 50)):
                    game_active = False

            if bird.pos_y <= 10 or bird.pos_y >= 475:
                game_active = False

            if frame % 90 == 0:
                pipes.append(Pipe(800))

        draw_score(score, screen)
        for pipe in pipes:
            pipe.draw(screen)

        if not game_active:
            game_over_screen(score, screen)

        pygame.display.update()

if __name__ == "__main__":
    main()