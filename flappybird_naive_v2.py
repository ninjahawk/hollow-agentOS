import pygame
import random
import sys

pygame.init()

WIDTH, HEIGHT = 400, 600
GRAVITY = 0.4
FLAP = -8
PIPE_SPEED = 3
PIPE_GAP = 160
FPS = 60

SKY   = (112, 197, 235)
GREEN = (83, 168, 50)
DARK  = (45, 90, 27)
YELLOW= (255, 215, 0)
WHITE = (255, 255, 255)
RED   = (220, 50, 50)

screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Flappy Bird — Naive build")
clock = pygame.time.Clock()
font_big  = pygame.font.SysFont("Arial", 42, bold=True)
font_med  = pygame.font.SysFont("Arial", 26)
font_small= pygame.font.SysFont("Arial", 18)


class Bird:
    X = 80
    R = 18

    def __init__(self):
        self.y    = HEIGHT // 2
        self.vy   = 0
        self.alive= True

    def flap(self):
        self.vy = FLAP

    def update(self):
        self.vy += GRAVITY
        self.y  += self.vy
        if self.y - self.R < 0 or self.y + self.R > HEIGHT:
            self.alive = False

    def draw(self, surf):
        cx, cy = self.X, int(self.y)
        pygame.draw.circle(surf, YELLOW, (cx, cy), self.R)
        pygame.draw.circle(surf, (200, 160, 0), (cx, cy), self.R, 2)
        pygame.draw.circle(surf, WHITE, (cx + 8, cy - 6), 6)
        pygame.draw.circle(surf, (0, 0, 0), (cx + 10, cy - 5), 3)
        pygame.draw.polygon(surf, (255, 140, 0),
                            [(cx + 14, cy), (cx + 22, cy - 3), (cx + 22, cy + 3)])

    @property
    def rect(self):
        return pygame.Rect(self.X - self.R + 4, int(self.y) - self.R + 4,
                           self.R * 2 - 8, self.R * 2 - 8)


class Pipe:
    W = 60

    def __init__(self, x):
        self.x    = x
        self.gap_y= random.randint(120, HEIGHT - 120 - PIPE_GAP)
        self.passed = False

    def update(self):
        self.x -= PIPE_SPEED

    def off_screen(self):
        return self.x + self.W < 0

    def rects(self):
        top = pygame.Rect(self.x, 0, self.W, self.gap_y)
        bot = pygame.Rect(self.x, self.gap_y + PIPE_GAP, self.W, HEIGHT)
        return top, bot

    def draw(self, surf):
        top, bot = self.rects()
        for r, cap_y in ((top, top.bottom - 12), (bot, bot.top)):
            pygame.draw.rect(surf, GREEN, r)
            pygame.draw.rect(surf, DARK,  r, 2)
            cap = pygame.Rect(r.x - 4, cap_y, self.W + 8, 12)
            pygame.draw.rect(surf, GREEN, cap)
            pygame.draw.rect(surf, DARK,  cap, 2)


def draw_ground(surf, offset):
    pygame.draw.rect(surf, (210, 180, 100), (0, HEIGHT - 40, WIDTH, 40))
    pygame.draw.rect(surf, (180, 150, 80),  (0, HEIGHT - 40, WIDTH, 4))


def title_screen(surf, hi):
    surf.fill(SKY)
    draw_ground(surf, 0)
    t = font_big.render("FLAPPY BIRD", True, WHITE)
    s = font_med.render("SPACE / Click to flap", True, WHITE)
    h = font_small.render(f"Best: {hi}", True, WHITE)
    surf.blit(t, t.get_rect(center=(WIDTH//2, 200)))
    surf.blit(s, s.get_rect(center=(WIDTH//2, 280)))
    surf.blit(h, h.get_rect(center=(WIDTH//2, 320)))


def game_over_screen(surf, score, hi):
    overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 140))
    surf.blit(overlay, (0, 0))
    go = font_big.render("GAME OVER", True, RED)
    sc = font_med.render(f"Score: {score}", True, WHITE)
    hs = font_med.render(f"Best:  {hi}", True, WHITE)
    re = font_small.render("SPACE / Click to restart", True, WHITE)
    surf.blit(go, go.get_rect(center=(WIDTH//2, 220)))
    surf.blit(sc, sc.get_rect(center=(WIDTH//2, 290)))
    surf.blit(hs, hs.get_rect(center=(WIDTH//2, 330)))
    surf.blit(re, re.get_rect(center=(WIDTH//2, 390)))


def run_game():
    bird  = Bird()
    pipes = [Pipe(WIDTH + 60)]
    score = 0
    frame = 0
    ground_offset = 0

    while True:
        clock.tick(FPS)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                if (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE) \
                   or event.type == pygame.MOUSEBUTTONDOWN:
                    bird.flap()

        bird.update()
        ground_offset += PIPE_SPEED
        frame += 1

        if frame % 75 == 0:
            pipes.append(Pipe(WIDTH + 20))
        pipes = [p for p in pipes if not p.off_screen()]

        for p in pipes:
            p.update()
            if not p.passed and p.x + p.W < Bird.X:
                p.passed = True
                score += 1
            top, bot = p.rects()
            if bird.rect.colliderect(top) or bird.rect.colliderect(bot):
                bird.alive = False

        screen.fill(SKY)
        for p in pipes:
            p.draw(screen)
        draw_ground(screen, ground_offset)
        bird.draw(screen)

        sc_txt = font_med.render(str(score), True, WHITE)
        screen.blit(sc_txt, sc_txt.get_rect(center=(WIDTH//2, 40)))
        pygame.display.flip()

        if not bird.alive:
            return score


def main():
    hi = 0
    state = "title"
    last_score = 0

    while True:
        if state == "title":
            clock.tick(FPS)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                    if (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE) \
                       or event.type == pygame.MOUSEBUTTONDOWN:
                        state = "play"
            title_screen(screen, hi)
            pygame.display.flip()

        elif state == "play":
            result = run_game()
            if result is None:
                pygame.quit(); sys.exit()
            hi = max(hi, result)
            state = "over"
            last_score = result

        elif state == "over":
            clock.tick(FPS)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                    if (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE) \
                       or event.type == pygame.MOUSEBUTTONDOWN:
                        state = "play"
            game_over_screen(screen, last_score, hi)
            pygame.display.flip()


if __name__ == "__main__":
    main()
