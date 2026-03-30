import sys
import random
import pygame

pygame.init()

# Constants
WIDTH, HEIGHT = 800, 500
GRAVITY = 0.5
FLAP_POWER = 7
PIPE_WIDTH = 80
PIPE_GAP = 180
PIPE_FREQUENCY = 90

# Colors
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)

class Bird(pygame.sprite.Sprite):
    def __init__(self):
        super().__init__()
        self.image = pygame.Surface((30, 30))
        pygame.draw.ellipse(self.image, WHITE, [0, 0, 30, 30])
        self.rect = self.image.get_rect()
        self.rect.center = (100, HEIGHT // 2)
        self.velocity = 0

    def update(self):
        self.velocity += GRAVITY
        self.rect.y += int(self.velocity)

        if self.rect.y > HEIGHT - 50:
            self.rect.y = HEIGHT - 50
            self.velocity = 0

class Pipe(pygame.sprite.Sprite):
    def __init__(self, height):
        super().__init__()
        self.image = pygame.Surface((PIPE_WIDTH, height))
        pygame.draw.rect(self.image, GREEN, [0, 0, PIPE_WIDTH, height])
        self.rect = self.image.get_rect()
        self.rect.x = WIDTH
        self.rect.y = -height // 2

    def update(self):
        self.rect.x -= 3

class Game:
    def __init__(self):
        pygame.display.set_caption("Flappy Bird")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 40)
        self.bird = Bird()
        self.pipes = []
        self.score = 0
        self.game_over = False

    def new_pipe(self):
        height = random.randint(100, HEIGHT - PIPE_GAP - 50)
        pipe = Pipe(height)
        pipe2 = Pipe(HEIGHT - PIPE_GAP - height)
        pipe.rect.y = height // 2
        pipe2.rect.y = HEIGHT - PIPE_GAP - (height // 2)
        self.pipes.append((pipe, pipe2))

    def update(self):
        if not self.game_over:
            self.bird.update()
            for pipe in self.pipes:
                pipe[0].update()
                pipe[1].update()

            if len(self.pipes) == 0 or self.pipes[-1][0].rect.x < WIDTH - PIPE_FREQUENCY:
                self.new_pipe()

        self.score_text = self.font.render(f"Score: {self.score}", True, WHITE)

    def draw(self):
        self.screen.fill((0, 0, 0))
        self.screen.blit(self.bird.image, self.bird.rect)
        for pipe in self.pipes:
            self.screen.blit(pipe[0].image, pipe[0].rect)
            self.screen.blit(pipe[1].image, pipe[1].rect)
        self.screen.blit(self.score_text, (10, 10))
        pygame.display.update()

    def check_collision(self):
        if self.bird.rect.y > HEIGHT - 50 or \
           pygame.sprite.spritecollideany(self.bird, self.pipes):
            self.game_over = True

    def run(self):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()

                if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    self.bird.velocity = -FLAP_POWER

            self.update()
            self.draw()
            self.check_collision()

            if self.game_over:
                self.screen.fill((0, 0, 0))
                game_over_text = self.font.render(f"Game Over! Score: {self.score}", True, WHITE)
                restart_text = self.font.render("Press SPACE to restart", True, WHITE)
                self.screen.blit(game_over_text, (WIDTH // 2 - game_over_text.get_width() // 2, HEIGHT // 2))
                self.screen.blit(restart_text, (WIDTH // 2 - restart_text.get_width() // 2, HEIGHT // 2 + 50))
                pygame.display.update()

                while True:
                    for event in pygame.event.get():
                        if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                            return self.__init__()

            self.clock.tick(60)

if __name__ == "__main__":
    game = Game()
    game.run()